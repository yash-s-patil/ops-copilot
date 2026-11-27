"""The last step of every request: one error body for every failure, and a size limit.

A failed tool call reaches the caller as `{code, message, retryable, details}` and nothing
else. Anything that is not one of our own errors is logged here and reported as INTERNAL,
so a stack trace, a query or a host name cannot travel in a result. Nothing a caller chose,
such as a tool name or an argument, is written to the log.
"""

import json
import traceback
from collections.abc import Collection

import mcp.types as mt
import pydantic
import structlog
from fastmcp.exceptions import McpError, NotFoundError, PromptError, ResourceError, ToolError
from fastmcp.exceptions import ValidationError as ArgumentsRejected
from fastmcp.prompts import PromptResult
from fastmcp.resources import ResourceResult
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult

from opscp_common.errors import ErrorBody, InternalError, OpsError, ValidationFailedError
from opscp_common.errors import NotFoundError as NothingByThatName
from opscp_common.hashing import JsonValue
from opscp_mcp.services.redaction import NAMES_KEPT, is_argument_name

_LOG = structlog.get_logger(__name__)
_PROTOCOL_ERRORS = (McpError, NotFoundError)
_ENCODING = "utf-8"
_FRAMES_LOGGED = 6
_CHAIN_DEPTH = 8
UNKNOWN_TOOL = "an unknown tool"
UNKNOWN_RESOURCE = "an unknown resource"
UNKNOWN_PROMPT = "an unknown prompt"


def _chain(error: BaseException) -> list[BaseException]:
    """The error and what led to it, outermost first."""
    chain: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and len(chain) < _CHAIN_DEPTH:
        chain.append(current)
        handled_first = None if current.__suppress_context__ else current.__context__
        current = current.__cause__ or handled_first
    return chain


def _our_error(error: BaseException) -> OpsError | None:
    """Our own error, raised by a handler and at most wrapped once by the framework."""
    for candidate in (error, error.__cause__):
        if isinstance(candidate, OpsError):
            return candidate
    return None


def _rejected_fields(error: BaseException) -> list[JsonValue] | None:
    """The arguments the framework refused, or None when that is not what went wrong.

    The framework raises these two types itself, before a handler runs; a validation error
    from inside a handler arrives wrapped and is a defect, not a bad argument. Only names
    that could be real argument names are reported, so the caller's own text is not handed
    back at length.
    """
    if not isinstance(error, ArgumentsRejected | pydantic.ValidationError):
        return None
    names = {
        str(issue["loc"][0])
        for invalid in _chain(error)
        if isinstance(invalid, pydantic.ValidationError)
        for issue in invalid.errors()
        if issue["loc"]
    }
    return [*sorted(name for name in names if is_argument_name(name))[:NAMES_KEPT]]


def refusal(error: Exception) -> ErrorBody | None:
    """The body for a failure the caller is told about, or None when it is a defect."""
    ours = _our_error(error)
    if ours is not None:
        return ours.body()
    fields = _rejected_fields(error)
    if fields is not None:
        return ValidationFailedError(
            "The arguments do not match what this accepts.", fields=fields
        ).body()
    return None


def reported(error: Exception, *, during: str) -> ErrorBody:
    """The body a caller is given for a failure. `during` must not be the caller's own text."""
    body = refusal(error)
    if body is not None:
        _LOG.info("request_refused", during=during, code=body.code.value)
        return body
    return defect(error, during=during)


def defect(error: BaseException, *, during: str) -> ErrorBody:
    """Logs a failure that is ours, by type and place, and gives the body that says only that."""
    root = _chain(error)[-1]
    _LOG.error("request_failed", during=during, error=type(root).__name__, where=_frames(root))
    return InternalError().body()


def _frames(error: BaseException) -> list[str]:
    """Where the failure happened, without the exception's text, which may quote data."""
    frames = traceback.extract_tb(error.__traceback__)[-_FRAMES_LOGGED:]
    return [f"{frame.filename}:{frame.lineno} in {frame.name}" for frame in frames]


def content_bytes(result: ToolResult) -> int:
    """The size of what a model reads: the result as text, or as structure if that is larger."""
    structured = json.dumps(result.structured_content, ensure_ascii=False, separators=(",", ":"))
    text = sum(
        len(block.text.encode(_ENCODING))
        for block in result.content
        if isinstance(block, mt.TextContent)
    )
    return max(len(structured.encode(_ENCODING)), text)


class ResultCap(Middleware):
    """A result too large for a model to read is a defect in the tool that built it."""

    def __init__(self, result_bytes_max: int) -> None:
        self._result_bytes_max = result_bytes_max

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        result = await call_next(context)
        size = content_bytes(result)
        if size > self._result_bytes_max:
            _LOG.error("result_too_large", during=context.message.name, size=size)
            raise InternalError
        return result


class Envelope(Middleware):
    """`known` holds the names this server gave its components; only those are logged."""

    def __init__(self, known: Collection[str]) -> None:
        self._known = frozenset(known)

    def _named(self, asked_for: str, otherwise: str) -> str:
        return asked_for if asked_for in self._known else otherwise

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        try:
            return await call_next(context)
        except McpError:
            raise
        except NotFoundError as error:
            _LOG.info("request_refused", during=UNKNOWN_TOOL, code="NOT_FOUND")
            body = NothingByThatName("There is nothing with that name here.").body()
            raise ToolError(body.model_dump_json()) from error
        except Exception as error:
            during = self._named(context.message.name, UNKNOWN_TOOL)
            body = reported(error, during=during)
            raise ToolError(body.model_dump_json()) from error

    async def on_read_resource(
        self,
        context: MiddlewareContext[mt.ReadResourceRequestParams],
        call_next: CallNext[mt.ReadResourceRequestParams, ResourceResult],
    ) -> ResourceResult:
        try:
            return await call_next(context)
        except _PROTOCOL_ERRORS:
            raise
        except Exception as error:
            during = self._named(str(context.message.uri), UNKNOWN_RESOURCE)
            body = reported(error, during=during)
            raise ResourceError(body.model_dump_json()) from error

    async def on_get_prompt(
        self,
        context: MiddlewareContext[mt.GetPromptRequestParams],
        call_next: CallNext[mt.GetPromptRequestParams, PromptResult],
    ) -> PromptResult:
        try:
            return await call_next(context)
        except _PROTOCOL_ERRORS:
            raise
        except Exception as error:
            during = self._named(context.message.name, UNKNOWN_PROMPT)
            body = reported(error, during=during)
            raise PromptError(body.model_dump_json()) from error

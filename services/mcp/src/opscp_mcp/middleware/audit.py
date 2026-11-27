"""Every tool call leaves one audit row and one log line with the same fields.

The row is written after the call, when its outcome is known, and before the caller is
answered. If the row cannot be written the caller is given an error and not the result, so
nothing is ever handed out without a record of it. A call the client abandons is recorded
too: what the tool did has still been done.

A call that carries out a write is the exception. Its row is stored by the write, in the
write's own transaction, so it is made before the answer exists and carries no result hash.
"""

import asyncio
import time
from collections.abc import Collection, Mapping

import mcp.types as mt
import structlog
from fastmcp.exceptions import NotFoundError
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import Tool, ToolResult

from opscp_common.errors import ErrorCode
from opscp_common.hashing import content_hash
from opscp_common.redaction import APPROVAL_ARGUMENT, redacted
from opscp_mcp.container import Services
from opscp_mcp.middleware.envelope import UNKNOWN_TOOL, content_bytes, refusal
from opscp_mcp.ports.controls import AuditAction, AuditEntry
from opscp_mcp.reliability.deadline import within
from opscp_mcp.services.recording import offered
from opscp_mcp.services.runs import run_named_in
from opscp_mcp.tools.runner import services_in

_LOG = structlog.get_logger(__name__)
_MS_PER_S = 1000
_CANCELLED = "CANCELLED"


def _error_code(failure: BaseException) -> str:
    if isinstance(failure, asyncio.CancelledError):
        return _CANCELLED
    if isinstance(failure, NotFoundError):
        return ErrorCode.NOT_FOUND.value
    body = refusal(failure) if isinstance(failure, Exception) else None
    return (ErrorCode.INTERNAL if body is None else body.code).value


class Audit(Middleware):
    def __init__(self, *, text_chars: int, timeout_s: int, writes: Collection[str] = ()) -> None:
        self._text_chars = text_chars
        self._timeout_s = timeout_s
        self._writes = frozenset(writes)

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        started = time.monotonic()
        request = context.fastmcp_context
        services = services_in(request)
        tool = None if request is None else await request.fastmcp.get_tool(context.message.name)

        def entry(result: ToolResult | None, failure: BaseException | None) -> AuditEntry:
            return self._entry(context, services, tool, started, result, failure)

        result: ToolResult | None = None
        failure: BaseException | None = None
        with offered(lambda refusal: entry(None, refusal)) as recording:
            try:
                result = await call_next(context)
            except (Exception, asyncio.CancelledError) as error:  # noqa: BLE001 -- recorded below, then raised again
                failure = error
        if recording.audit_id is not None and recording.stored is not None:
            stored = recording.stored.model_dump(mode="json")
            _LOG.info("audit", audit_id=recording.audit_id, **stored)
        else:
            # Recorded outside the handler above so that a failure to record is reported as
            # itself, and shielded so that a client that has gone away cannot stop it.
            await asyncio.shield(self._record(services, entry(result, failure)))
        if failure is not None:
            raise failure
        if result is None:
            raise AssertionError
        return result

    def _entry(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        services: Services,
        tool: Tool | None,
        started: float,
        result: ToolResult | None,
        failure: BaseException | None,
    ) -> AuditEntry:
        caller = services.identities.current()
        arguments = context.message.arguments or {}
        declared = () if tool is None else tool.parameters.get("properties", {})
        code = None if failure is None else _error_code(failure)
        denied = code == ErrorCode.PERMISSION_DENIED.value
        return AuditEntry(
            run_id=run_named_in(get_http_headers()),
            actor_oid=caller.oid,
            actor_roles=tuple(sorted(caller.roles)),
            actor_kind="user",
            target=UNKNOWN_TOOL if tool is None else tool.name,
            action="deny" if denied else self._action(context.message.name, arguments),
            args_redacted=redacted(arguments, declared, self._text_chars),
            outcome="denied" if denied else "ok" if code is None else "error",
            error_code=code,
            result_hash=None if result is None else content_hash(result.structured_content),
            result_bytes=None if result is None else content_bytes(result),
            duration_ms=round((time.monotonic() - started) * _MS_PER_S),
        )

    def _action(self, name: str, arguments: Mapping[str, object]) -> AuditAction:
        """A write tool asks unless it brings an approval; then it carries the write out."""
        if name not in self._writes:
            return "call"
        return "execute" if arguments.get(APPROVAL_ARGUMENT) is not None else "propose"

    async def _record(self, services: Services, entry: AuditEntry) -> None:
        audit_id = await within(self._timeout_s, services.audit.record(entry))
        _LOG.info("audit", audit_id=audit_id, **entry.model_dump(mode="json"))

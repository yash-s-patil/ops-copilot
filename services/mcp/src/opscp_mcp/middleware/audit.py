"""Every tool call leaves one audit row and one log line with the same fields.

The row is written after the call, when its outcome is known, and before the caller is
answered. If the row cannot be written the caller is given an error and not the result, so
nothing is ever handed out without a record of it. A call the client abandons is recorded
too: what the tool did has still been done.
"""

import asyncio
import time

import mcp.types as mt
import structlog
from fastmcp.exceptions import NotFoundError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult

from opscp_common.errors import ErrorCode
from opscp_common.hashing import content_hash
from opscp_mcp.middleware.envelope import UNKNOWN_TOOL, content_bytes, refusal
from opscp_mcp.ports.controls import AuditEntry
from opscp_mcp.reliability.deadline import within
from opscp_mcp.services.redaction import redacted
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
    def __init__(self, *, text_chars: int, timeout_s: int) -> None:
        self._text_chars = text_chars
        self._timeout_s = timeout_s

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        started = time.monotonic()
        result: ToolResult | None = None
        failure: BaseException | None = None
        try:
            result = await call_next(context)
        except (Exception, asyncio.CancelledError) as error:  # noqa: BLE001 -- recorded below, then raised again
            failure = error
        # Recorded outside the handler above so that a failure to record is reported as
        # itself, and shielded so that a client that has gone away cannot stop the record.
        await asyncio.shield(self._record(context, started, result, failure))
        if failure is not None:
            raise failure
        if result is None:
            raise AssertionError
        return result

    async def _record(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        started: float,
        result: ToolResult | None,
        failure: BaseException | None,
    ) -> None:
        request = context.fastmcp_context
        services = services_in(request)
        caller = services.identities.current()
        tool = None if request is None else await request.fastmcp.get_tool(context.message.name)
        declared = () if tool is None else tool.parameters.get("properties", {})
        code = None if failure is None else _error_code(failure)
        denied = code == ErrorCode.PERMISSION_DENIED.value
        entry = AuditEntry(
            actor_oid=caller.oid,
            actor_roles=tuple(sorted(caller.roles)),
            actor_kind="user",
            target=UNKNOWN_TOOL if tool is None else tool.name,
            action="deny" if denied else "call",
            args_redacted=redacted(context.message.arguments or {}, declared, self._text_chars),
            outcome="denied" if denied else "ok" if code is None else "error",
            error_code=code,
            result_hash=None if result is None else content_hash(result.structured_content),
            result_bytes=None if result is None else content_bytes(result),
            duration_ms=round((time.monotonic() - started) * _MS_PER_S),
        )
        audit_id = await within(self._timeout_s, services.audit.record(entry))
        _LOG.info("audit", audit_id=audit_id, **entry.model_dump(mode="json"))

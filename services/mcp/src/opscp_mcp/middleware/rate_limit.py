"""Each person may make a limited number of requests a minute.

The count is taken before anything else is done for the request, and by person, so one
engineer's runaway agent cannot slow the service for the rest.
"""

import mcp.types as mt
import structlog
from fastmcp import Context
from fastmcp.prompts import PromptResult
from fastmcp.resources import ResourceResult
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult

from opscp_common.errors import RateLimitedError
from opscp_mcp.tools.runner import services_in, within

_LOG = structlog.get_logger(__name__)


class RateLimit(Middleware):
    def __init__(self, timeout_s: int) -> None:
        self._timeout_s = timeout_s

    async def _count(self, request: Context | None) -> None:
        services = services_in(request)
        caller = services.identities.current()
        allowance = await within(self._timeout_s, services.rates.take(caller.oid))
        if not allowance.allowed:
            _LOG.info(
                "rate_limited", actor_oid=str(caller.oid), retry_after_s=allowance.retry_after_s
            )
            raise RateLimitedError(
                "Too many requests. Wait before trying again.",
                retry_after_seconds=allowance.retry_after_s,
            )

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        await self._count(context.fastmcp_context)
        return await call_next(context)

    async def on_read_resource(
        self,
        context: MiddlewareContext[mt.ReadResourceRequestParams],
        call_next: CallNext[mt.ReadResourceRequestParams, ResourceResult],
    ) -> ResourceResult:
        await self._count(context.fastmcp_context)
        return await call_next(context)

    async def on_get_prompt(
        self,
        context: MiddlewareContext[mt.GetPromptRequestParams],
        call_next: CallNext[mt.GetPromptRequestParams, PromptResult],
    ) -> PromptResult:
        await self._count(context.fastmcp_context)
        return await call_next(context)

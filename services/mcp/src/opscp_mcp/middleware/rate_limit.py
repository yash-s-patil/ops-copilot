"""Each person may make a limited number of requests a minute.

The count is taken before anything else is done for the request, and by person, so one
engineer's runaway agent cannot slow the service for the rest.
"""

from collections.abc import Collection

import mcp.types as mt
from fastmcp import Context
from fastmcp.prompts import PromptResult
from fastmcp.resources import ResourceResult
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult

from opscp_mcp.services.rate import count_request
from opscp_mcp.tools.runner import services_in


class RateLimit(Middleware):
    """`uncounted` resources are answered from memory and must stay readable when the
    database that holds the counts does not answer."""

    def __init__(self, timeout_s: int, uncounted: Collection[str] = ()) -> None:
        self._timeout_s = timeout_s
        self._uncounted = frozenset(uncounted)

    async def _count(self, request: Context | None) -> None:
        services = services_in(request)
        await count_request(services.rates, services.identities.current(), self._timeout_s)

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
        if str(context.message.uri) not in self._uncounted:
            await self._count(context.fastmcp_context)
        return await call_next(context)

    async def on_get_prompt(
        self,
        context: MiddlewareContext[mt.GetPromptRequestParams],
        call_next: CallNext[mt.GetPromptRequestParams, PromptResult],
    ) -> PromptResult:
        await self._count(context.fastmcp_context)
        return await call_next(context)

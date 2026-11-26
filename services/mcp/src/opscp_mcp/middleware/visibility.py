"""Lists show a caller only what their permissions let them use.

Hiding is a courtesy to the model, not the control: every call checks the permission again
in the service that answers it. Anything without a declared permission is hidden.
"""

from collections.abc import Mapping, Sequence

import mcp.types as mt
from fastmcp import Context
from fastmcp.prompts import Prompt
from fastmcp.resources import Resource, ResourceTemplate
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import Tool

from opscp_common.errors import PermissionDeniedError
from opscp_mcp.authz.identity import Identity
from opscp_mcp.authz.policy import Permission
from opscp_mcp.tools.runner import services_in


def _caller(request: Context | None) -> Identity | None:
    try:
        return services_in(request).identities.current()
    except PermissionDeniedError:
        return None


class Visibility(Middleware):
    def __init__(self, requires: Mapping[str, Permission]) -> None:
        self._requires = requires

    def _usable(self, caller: Identity | None, name: str) -> bool:
        needed = self._requires.get(name)
        return caller is not None and needed is not None and caller.can(needed)

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        caller = _caller(context.fastmcp_context)
        return [tool for tool in await call_next(context) if self._usable(caller, tool.name)]

    async def on_list_resources(
        self,
        context: MiddlewareContext[mt.ListResourcesRequest],
        call_next: CallNext[mt.ListResourcesRequest, Sequence[Resource]],
    ) -> Sequence[Resource]:
        caller = _caller(context.fastmcp_context)
        return [
            resource
            for resource in await call_next(context)
            if self._usable(caller, str(resource.uri))
        ]

    async def on_list_resource_templates(
        self,
        context: MiddlewareContext[mt.ListResourceTemplatesRequest],
        call_next: CallNext[mt.ListResourceTemplatesRequest, Sequence[ResourceTemplate]],
    ) -> Sequence[ResourceTemplate]:
        caller = _caller(context.fastmcp_context)
        return [
            template
            for template in await call_next(context)
            if self._usable(caller, template.uri_template)
        ]

    async def on_list_prompts(
        self,
        context: MiddlewareContext[mt.ListPromptsRequest],
        call_next: CallNext[mt.ListPromptsRequest, Sequence[Prompt]],
    ) -> Sequence[Prompt]:
        caller = _caller(context.fastmcp_context)
        return [prompt for prompt in await call_next(context) if self._usable(caller, prompt.name)]

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import mcp.types as mt
import pytest
from fastmcp import FastMCP
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools import Tool

from opscp_common.errors import ErrorCode, InternalError
from opscp_common.limits import Limits
from opscp_mcp.authz.policy import Permission
from opscp_mcp.authz.tokens import TokenIdentities
from opscp_mcp.container import Services
from opscp_mcp.middleware.visibility import Visibility
from opscp_mcp.prompts import procedures
from opscp_mcp.tools.names import GET_ORDER
from opscp_mcp.tools.runner import SERVICES_KEY
from opscp_mcp_testing.identities import PRIYA, SAM
from opscp_mcp_testing.server import (
    SignedOut,
    call,
    connected,
    error_of,
    fake_services,
    server_for,
)
from opscp_mcp_testing.world import STUCK_ON_ADDRESS


def _server(services: Services, requires: dict[str, Permission]) -> FastMCP:
    @asynccontextmanager
    async def lifespan(_server: FastMCP) -> AsyncGenerator[dict[str, Services]]:
        yield {SERVICES_KEY: services}

    server = FastMCP("visibility", lifespan=lifespan, middleware=[Visibility(requires)])

    @server.tool
    def declared() -> str:
        return "declared"

    @server.tool
    def undeclared() -> str:
        return "undeclared"

    @server.resource("test://declared")
    def declared_resource() -> str:
        return "declared"

    @server.resource("test://undeclared")
    def undeclared_resource() -> str:
        return "undeclared"

    @server.resource("test://declared/{part}")
    def declared_template(part: str) -> str:
        return part

    @server.resource("test://undeclared/{part}")
    def undeclared_template(part: str) -> str:
        return part

    @server.prompt
    def declared_prompt() -> str:
        return "declared"

    @server.prompt
    def undeclared_prompt() -> str:
        return "undeclared"

    return server


REQUIRES = {
    "declared": Permission.ORDERS_READ,
    "test://declared": Permission.ORDERS_READ,
    "test://declared/{part}": Permission.ORDERS_READ,
    "declared_prompt": Permission.ORDERS_READ,
}


async def test_anything_without_a_declared_permission_is_hidden(limits: Limits) -> None:
    async with connected(_server(fake_services(PRIYA, limits), REQUIRES)) as priya:
        tools = [tool.name for tool in await priya.list_tools()]
        resources = [str(resource.uri) for resource in await priya.list_resources()]
        templates = [t.uri_template for t in await priya.list_resource_templates()]
        prompts = [prompt.name for prompt in await priya.list_prompts()]

    assert (tools, resources, prompts) == (["declared"], ["test://declared"], ["declared_prompt"])
    assert templates == ["test://declared/{part}"]


async def test_a_caller_without_the_permission_is_shown_nothing(limits: Limits) -> None:
    async with connected(_server(fake_services(SAM, limits), REQUIRES)) as sam:
        shown = (
            await sam.list_tools(),
            await sam.list_resources(),
            await sam.list_resource_templates(),
            await sam.list_prompts(),
        )

    assert shown == ([], [], [], [])


async def test_a_request_with_no_caller_is_shown_nothing(limits: Limits) -> None:
    async with connected(server_for(PRIYA, limits, identities=SignedOut())) as nobody:
        shown = (
            await nobody.list_tools(),
            await nobody.list_resources(),
            await nobody.list_prompts(),
        )

    assert shown == ([], [], [])


async def test_every_registered_component_declares_its_permission(limits: Limits) -> None:
    """Fails when a tool, resource or prompt is added without saying who may use it."""
    server = server_for(PRIYA, limits)
    registered = (
        [tool.name for tool in await server.list_tools(run_middleware=False)]
        + [str(resource.uri) for resource in await server.list_resources(run_middleware=False)]
        + [t.uri_template for t in await server.list_resource_templates(run_middleware=False)]
        + [prompt.name for prompt in await server.list_prompts(run_middleware=False)]
    )

    async with connected(server_for(PRIYA, limits)) as priya:
        shown = (
            [tool.name for tool in await priya.list_tools()]
            + [str(resource.uri) for resource in await priya.list_resources()]
            + [t.uri_template for t in await priya.list_resource_templates()]
            + [prompt.name for prompt in await priya.list_prompts()]
        )

    assert len(registered) == 14
    assert sorted([*shown, "issue_refund"]) == sorted(registered)


def test_two_components_cannot_share_a_name(
    monkeypatch: pytest.MonkeyPatch, limits: Limits
) -> None:
    monkeypatch.setattr(procedures, "REQUIRES", {GET_ORDER: Permission.TICKETS_READ})

    with pytest.raises(ValueError, match="share a name"):
        server_for(PRIYA, limits)


async def test_a_caller_read_from_tokens_is_nobody_when_no_token_was_verified(
    limits: Limits,
) -> None:
    async with connected(server_for(PRIYA, limits, identities=TokenIdentities())) as unverified:
        shown = await unverified.list_tools()
        refused = error_of(await call(unverified, GET_ORDER, order_id=STUCK_ON_ADDRESS))

    assert shown == []
    assert (refused.code, refused.message) == (
        ErrorCode.PERMISSION_DENIED,
        "Sign in to use this server.",
    )


async def test_a_list_asked_for_outside_a_request_is_a_defect(limits: Limits) -> None:
    outside_a_request = MiddlewareContext(message=mt.ListToolsRequest(), method="tools/list")

    async def never_reached(context: MiddlewareContext[mt.ListToolsRequest]) -> list[Tool]:
        raise AssertionError

    with pytest.raises(InternalError):
        await Visibility(REQUIRES).on_list_tools(outside_a_request, never_reached)

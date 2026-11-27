"""An MCP server wired to the in-memory fakes, called as one fixed person."""

import itertools
import json
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any

from fastmcp import Client, FastMCP
from fastmcp.client.client import CallToolResult
from fastmcp.client.transports import FastMCPTransport
from mcp.types import TextContent, TextResourceContents

from opscp_common.errors import ErrorBody, PermissionDeniedError
from opscp_common.hashing import JsonValue
from opscp_common.limits import Limits
from opscp_mcp.adapters.local_signer import LocalApprovalSigner
from opscp_mcp.adapters.memory_runbooks import InMemoryRunbooks
from opscp_mcp.authz.identity import Identity
from opscp_mcp.container import Services
from opscp_mcp.server import build_server
from opscp_mcp.services.approvals import ApprovalService
from opscp_mcp.services.customers import CustomerService
from opscp_mcp.services.health import HealthService
from opscp_mcp.services.orders import OrderService
from opscp_mcp.services.policies import PolicyService
from opscp_mcp.services.proposals import ProposalService
from opscp_mcp.services.resources import ReferenceService
from opscp_mcp.services.runbooks import RunbookService
from opscp_mcp.services.tickets import TicketService
from opscp_mcp_testing.fakes.actions import FakePendingActions, FakeRefundCases
from opscp_mcp_testing.fakes.controls import FakeAuditTrail, FakeRateLimiter
from opscp_mcp_testing.fakes.knowledge import FakePolicies
from opscp_mcp_testing.fakes.meridian import FakeCustomers, FakeOrders, FakeTickets
from opscp_mcp_testing.paths import POLICIES_DIR, RUNBOOKS_DIR
from opscp_mcp_testing.signin import approval_key_pem
from opscp_mcp_testing.world import NOW, WORLD, World

UNLIMITED = 1_000_000


class Caller:
    """Stands in for sign-in: every request is made by the identity it holds."""

    def __init__(self, identity: Identity) -> None:
        self._identity = identity

    def current(self) -> Identity:
        return self._identity


class SignedOut:
    """A request that reached the server with no verified caller."""

    def current(self) -> Identity:
        raise PermissionDeniedError("Sign in to use this server.")


def fake_services(identity: Identity, limits: Limits, world: World = WORLD) -> Services:
    runbooks = InMemoryRunbooks.from_directory(RUNBOOKS_DIR)
    numbers = itertools.count(1)
    actions = FakePendingActions()
    return Services(
        identities=Caller(identity),
        rates=FakeRateLimiter(
            per_minute=limits.rate.calls_per_minute, burst=UNLIMITED, clock=lambda: NOW
        ),
        audit=FakeAuditTrail(),
        customers=CustomerService(FakeCustomers(world)),
        orders=OrderService(FakeOrders(world), limits, lambda: NOW),
        tickets=TicketService(FakeTickets(world), limits, lambda: NOW),
        proposals=ProposalService(
            FakeTickets(world), FakeRefundCases(world, actions), actions, limits, lambda: NOW
        ),
        approvals=ApprovalService(
            actions, LocalApprovalSigner(approval_key_pem()), limits, lambda: NOW
        ),
        runbooks=RunbookService(runbooks, limits),
        policies=PolicyService(FakePolicies.from_directory(POLICIES_DIR), limits),
        references=ReferenceService(runbooks, limits),
        health=HealthService((), {}),
        clock=lambda: NOW,
        new_result_id=lambda: f"{next(numbers):04d}",
    )


def server_with(services: Services, limits: Limits) -> FastMCP:
    @asynccontextmanager
    async def open_services() -> AsyncGenerator[Services]:
        yield services

    return build_server(open_services, limits)


def server_for(identity: Identity, limits: Limits, **changed: object) -> FastMCP:
    """A server over the shared test world, with any of its services replaced."""
    return server_with(replace(fake_services(identity, limits), **changed), limits)


type TestClient = Client[FastMCPTransport]


@asynccontextmanager
async def connected(server: FastMCP) -> AsyncGenerator[TestClient]:
    async with Client(FastMCPTransport(server)) as client:
        yield client


async def call(client: TestClient, tool: str, **arguments: JsonValue) -> CallToolResult:
    return await client.call_tool(tool, dict(arguments), raise_on_error=False)


async def call_with(
    client: TestClient, tool: str, arguments: Mapping[str, object]
) -> CallToolResult:
    """Like `call`, for arguments a well-behaved client would never send."""
    return await client.call_tool(tool, dict(arguments), raise_on_error=False)


def text_of(block: object) -> str:
    assert isinstance(block, TextContent | TextResourceContents)
    return block.text


def data_of(result: CallToolResult) -> dict[str, Any]:
    assert not result.is_error, result.content
    assert result.structured_content is not None
    return result.structured_content["data"]


def error_of(result: CallToolResult) -> ErrorBody:
    """The error body of a failed call, which must be the only thing in the result."""
    assert result.is_error
    assert result.structured_content is None
    (block,) = result.content
    return ErrorBody.model_validate(json.loads(text_of(block)))

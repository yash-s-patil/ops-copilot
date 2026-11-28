"""The agent service wired to a real MCP application, both in this process.

The MCP application is the one that is deployed, over the in-memory fakes of its own test
suite. The agent reaches it through the same client it uses over the network, with tokens
exchanged by the local issuer's own code. Only the model and the two stores are stand-ins.
"""

import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx2
from fastapi import FastAPI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import create_async_engine

from opscp_agent.api.access import Agent
from opscp_agent.auth.obo import AccessToken, ReauthRequiredError, TokenClient
from opscp_agent.auth.verifier import Callers, token_verifier
from opscp_agent.events.bus import Bus
from opscp_agent.graph.build import build_graph
from opscp_agent.llm.base import ChatModel
from opscp_agent.llm.scripted import ScriptedModel
from opscp_agent.main import build_app
from opscp_agent.mcp_client.client import NO_PATIENCE, McpGateway, Patience
from opscp_agent.mcp_client.resource_cache import ResourceCache
from opscp_agent.runs.manager import RunManager, Workings
from opscp_agent.runs.store import RunStore
from opscp_agent.settings import DevSignIn
from opscp_agent.usage.cost import CostEstimate, Pricing
from opscp_agent_testing.memory_store import InMemoryRunStore
from opscp_common.limits import Limits
from opscp_dev_issuer.exchange import GRANT_TYPE, TOKEN_USE, Client, Exchange, RefusedError
from opscp_mcp.authz.identity import Identity
from opscp_mcp.authz.tokens import TokenIdentities
from opscp_mcp.container import Services
from opscp_mcp.main import build_app as build_mcp_app
from opscp_mcp_testing.fakes.controls import FakeAuditTrail
from opscp_mcp_testing.fakes.execution import Written
from opscp_mcp_testing.identities import PRIYA
from opscp_mcp_testing.server import fake_services, fake_writes
from opscp_mcp_testing.signin import (
    AUDIENCE as MCP_AUDIENCE,
)
from opscp_mcp_testing.signin import (
    ISSUER,
    claims_of,
    dev_sign_in,
    public_pem,
    signed,
    signing_key,
)
from opscp_mcp_testing.world import NOW, World

AGENT_AUDIENCE = "api://opscp-agent"
AGENT_SCOPE = "Agent.Access"
CLIENT_ID = "opscp-agent-api"
CLIENT_SECRET = "a-secret-for-tests-and-nothing-else"  # noqa: S105 -- made for the test run
CANARY = "canary-7f3a9c2e5b8d4f61"
MCP_URL = "http://mcp.test"
AGENT_URL = "http://agent.test"
NOWHERE = "postgresql+asyncpg://nobody:unused@127.0.0.1:1/nothing"
WAIT_S = 10


def token_for(identity: Identity, **changed: Any) -> str:
    """A token as sign-in gives the web application: meant for the agent, not for MCP."""
    return signed(claims_of(identity, aud=AGENT_AUDIENCE, scp=AGENT_SCOPE, **changed))


def bearer(identity: Identity, **changed: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {token_for(identity, **changed)}"}


class LocalIssuer:
    """The exchange made by the local issuer's own code, without the network between."""

    def __init__(self) -> None:
        self.exchanges = 0
        self._exchange = Exchange(
            ISSUER,
            signing_key().as_pem(private=True).decode("ascii"),
            Client(
                client_id=CLIENT_ID,
                secret=CLIENT_SECRET,
                audience=AGENT_AUDIENCE,
                downstream_audience=MCP_AUDIENCE,
            ),
        )

    async def exchange(self, assertion: str) -> AccessToken:
        self.exchanges += 1
        try:
            issued = self._exchange.on_behalf_of(
                {
                    "grant_type": GRANT_TYPE,
                    "requested_token_use": TOKEN_USE,
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                    "scope": f"{MCP_AUDIENCE}/.default",
                    "assertion": assertion,
                }
            )
        except RefusedError as refused:
            raise ReauthRequiredError from refused
        return AccessToken(issued.access_token, time.time() + issued.expires_in)


@dataclass(slots=True)
class Desk:
    agent: httpx2.AsyncClient
    mcp_app: FastAPI
    store: InMemoryRunStore
    manager: RunManager
    workings: Workings
    issuer: LocalIssuer
    audit: FakeAuditTrail
    written: Written
    mcp_requests: list[httpx2.Request] = field(default_factory=list[httpx2.Request])

    async def start(self, who: Identity, message: str, ticket_id: int | None = None) -> UUID:
        body: dict[str, Any] = {"message": message}
        if ticket_id is not None:
            body["ticket_id"] = ticket_id
        response = await self.agent.post("/api/runs", json=body, headers=bearer(who))
        assert response.status_code == 202, response.text
        return UUID(response.json()["run_id"])

    async def settled(self, run_id: UUID, *statuses: str) -> str:
        """Waits for the run to reach one of the statuses, and for its work to be put down."""
        deadline = time.monotonic() + WAIT_S
        while time.monotonic() < deadline:
            run = self.store.runs[run_id]
            if run.status in statuses and run_id not in self.store.leases:
                return run.status
            await self.manager.idle()
        raise AssertionError(f"run is {self.store.runs[run_id].status}, wanted {statuses}")

    async def decide(self, who: Identity, run_id: UUID, *, approve: bool = True) -> None:
        """Clicks Approve, or Reject, on every change that is the requester's own to decide."""
        for asked in self.store.payloads(run_id, "approval.required"):
            action = f"/api/runs/{run_id}/actions/{asked['action_id']}"
            resolved = {
                event["action_id"] for event in self.store.payloads(run_id, "approval.resolved")
            }
            if asked["action_id"] in resolved or asked["approval_kind"] != "self":
                continue
            response = (
                await self.agent.post(
                    f"{action}/approve", json={"args_hash": asked["args_hash"]}, headers=bearer(who)
                )
                if approve
                else await self.agent.post(
                    f"{action}/reject", json={"reason": "Not now."}, headers=bearer(who)
                )
            )
            assert response.status_code == 200, response.text


def _pricing() -> Pricing:
    return Pricing.model_validate(
        {"per_tokens": 1_000_000, "models": {"priced": {"input": "2", "output": "10"}}}
    )


@dataclass(frozen=True, slots=True)
class McpBackend:
    """What the MCP application keeps, which outlives any one agent process."""

    services: Services
    audit: FakeAuditTrail
    written: Written


def mcp_backend(limits: Limits, world: World, **changed: Any) -> McpBackend:
    base = fake_services(PRIYA, limits, world)
    assert isinstance(base.audit, FakeAuditTrail)
    writes = fake_writes(world, limits, base.audit)
    services = replace(
        base,
        identities=TokenIdentities(),
        proposals=writes.proposals,
        approvals=writes.approvals,
        refunds=writes.refunds,
        **changed,
    )
    return McpBackend(services, base.audit, writes.executions.written)


@asynccontextmanager
async def desk(  # noqa: PLR0913 -- each is one part a test may replace
    limits: Limits,
    world: World,
    *,
    model: ChatModel | None = None,
    demo_tools: bool = False,
    mcp_handler: Callable[[httpx2.Request], httpx2.Response | None] | None = None,
    mcp_services: dict[str, Any] | None = None,
    saver: BaseCheckpointSaver[Any] | None = None,
    store: RunStore | None = None,
    backend: McpBackend | None = None,
    route_timeout_s: int = WAIT_S,
    storage_ready: Callable[[], Awaitable[None]] | None = None,
    run_patience: Patience = NO_PATIENCE,
) -> AsyncGenerator[Desk]:
    """`mcp_handler` may answer a request in the MCP server's place, by returning a response.

    `mcp_services` replaces services of the MCP application, for a backend that misbehaves.
    `saver`, `store` and `backend` are given when a test starts the agent a second time
    over what the first left behind.
    """
    behind = backend or mcp_backend(limits, world, **(mcp_services or {}))
    services = behind.services

    @asynccontextmanager
    async def open_services() -> AsyncGenerator[Any]:
        yield services

    mcp_app = build_mcp_app(create_async_engine(NOWHERE), open_services, limits, dev_sign_in())
    seen: list[httpx2.Request] = []
    inner = httpx2.ASGITransport(app=mcp_app)

    class Recording(httpx2.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
            seen.append(request)
            answered = mcp_handler(request) if mcp_handler else None
            return answered or await inner.handle_async_request(request)

    def http_factory(**kwargs: Any) -> httpx2.AsyncClient:
        kwargs.pop("transport", None)
        return httpx2.AsyncClient(transport=Recording(), **kwargs)

    issuer = LocalIssuer()
    memory = InMemoryRunStore()
    store = store or memory
    bus = Bus()
    cost = CostEstimate(_pricing(), Decimal(90))
    gateway = McpGateway(
        MCP_URL,
        TokenClient(issuer, refresh_before_s=limits.token.refresh_before_expiry_s, kept=16),
        timeout_s=limits.mcp_client.timeout_s,
        run_patience=run_patience,
        http_factory=http_factory,
    )

    async def always_ready() -> None:
        return None

    workings = Workings(
        graph=build_graph(saver or InMemorySaver()),
        store=store,
        bus=bus,
        gateway=gateway,
        model=model or ScriptedModel(),
        cost=cost,
        resources=ResourceCache(limits.health.cache_s),
        limits=limits,
        canary=CANARY,
        storage_ready=storage_ready or always_ready,
        now=lambda: NOW,
    )
    manager = RunManager(workings)
    agent = Agent(
        callers=Callers(
            token_verifier(
                DevSignIn(issuer=ISSUER, audience=AGENT_AUDIENCE, public_key_pem=public_pem())
            )
        ),
        gateway=gateway,
        manager=manager,
        store=store,
        bus=bus,
        cost=cost,
        limits=limits,
        demo_tools=demo_tools,
        route_timeout_s=route_timeout_s,
    )

    async def ready() -> bool:
        return True

    @asynccontextmanager
    async def opened() -> AsyncGenerator[tuple[Agent, Any]]:
        yield agent, ready

    app = build_app(opened, limits, demo_tools=demo_tools)
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(mcp_app.router.lifespan_context(mcp_app))
        await stack.enter_async_context(app.router.lifespan_context(app))
        client = await stack.enter_async_context(
            httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=AGENT_URL)
        )
        yield Desk(
            agent=client,
            mcp_app=mcp_app,
            store=memory,
            manager=manager,
            workings=workings,
            issuer=issuer,
            audit=behind.audit,
            written=behind.written,
            mcp_requests=seen,
        )

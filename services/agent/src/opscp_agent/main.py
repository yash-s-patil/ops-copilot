"""The web application of the agent service."""

import ssl
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import httpx2
from azure.identity.aio import ManagedIdentityCredential
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from starlette.exceptions import HTTPException as StarletteHTTPException

from opscp_agent.adapters.checkpoints import Checkpoints, checkpoint_pool
from opscp_agent.adapters.postgres import PostgresRunStore
from opscp_agent.api import admin, approvals, debug, me, refunds, runs, tickets
from opscp_agent.api.access import (
    Agent,
    NotSignedInError,
    invalid_request,
    unauthorised,
    unreadable,
)
from opscp_agent.api.health import health_router
from opscp_agent.auth.obo import (
    ClientProof,
    ExchangeTarget,
    FederatedProof,
    OnBehalfOf,
    SecretProof,
    TokenClient,
)
from opscp_agent.auth.verifier import Callers, token_verifier
from opscp_agent.events.bus import Bus
from opscp_agent.graph.build import build_graph
from opscp_agent.llm.base import ChatModel
from opscp_agent.llm.dependable import Dependable
from opscp_agent.llm.scripted import ScriptedModel
from opscp_agent.mcp_client.client import McpGateway
from opscp_agent.mcp_client.resource_cache import ResourceCache
from opscp_agent.runs.manager import RunManager, Workings
from opscp_agent.settings import SERVICE_NAME, AgentSettings, FederatedExchange, SecretExchange
from opscp_agent.telemetry.logs import adopt_library_logs
from opscp_agent.usage.cost import CostEstimate, load_pricing
from opscp_common.limits import Limits, load_limits
from opscp_common.logging import configure_logging
from opscp_common.settings import LOCAL, DatabaseSettings, Environment
from opscp_common.web.body import SmallBodies
from opscp_common.web.origin import NoBrowserOrigins

API_PATH = "/api"
TITLE = "Meridian Ops Copilot agent"
_PING = text("SELECT 1")

type Opened = Callable[[], AbstractAsyncContextManager[tuple[Agent, Callable[[], Awaitable[bool]]]]]


def build_app(opened: Opened, limits: Limits, *, demo_tools: bool) -> FastAPI:
    """The application around a way to open what it works with.

    No browser page is a caller of this service: the web application calls it from its own
    server. A request that names an origin is refused.
    """
    ready_checks: list[Callable[[], Awaitable[bool]]] = []

    async def ready() -> bool:
        return bool(ready_checks) and await ready_checks[0]()

    @asynccontextmanager
    async def run(app: FastAPI) -> AsyncGenerator[None]:
        async with opened() as (agent, check):
            app.state.agent = agent
            ready_checks.append(check)
            try:
                yield
            finally:
                await agent.manager.close()

    app = FastAPI(title=TITLE, lifespan=run, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SmallBodies, under=(API_PATH,), max_bytes=limits.runs.body_bytes_max)
    app.add_middleware(NoBrowserOrigins)
    app.add_exception_handler(NotSignedInError, unauthorised)
    app.add_exception_handler(StarletteHTTPException, unreadable)
    app.add_exception_handler(RequestValidationError, invalid_request)
    app.include_router(health_router(ready))
    for routes in (me, tickets, runs, approvals, refunds, admin):
        app.include_router(routes.router)
    if demo_tools:
        app.include_router(debug.router)
    return app


def database_engine(
    database: DatabaseSettings, environment: Environment, connections: int
) -> AsyncEngine:
    """Outside local development the connection is encrypted and the server's name checked."""
    encrypted = {} if environment == LOCAL else {"ssl": ssl.create_default_context()}
    return create_async_engine(
        database.url(),
        hide_parameters=True,
        connect_args=encrypted,
        pool_size=connections,
        max_overflow=0,
        pool_pre_ping=True,
    )


def chat_model(settings: AgentSettings, limits: Limits) -> ChatModel:
    """Settings refuse any provider this version cannot serve, so only one remains."""
    if settings.llm_provider != "scripted":
        raise ValueError("No model is available for this LLM_PROVIDER")
    return Dependable(ScriptedModel(), limits.model)


def production(settings: AgentSettings, database: DatabaseSettings, limits: Limits) -> Opened:
    @asynccontextmanager
    async def opened() -> AsyncGenerator[tuple[Agent, Callable[[], Awaitable[bool]]]]:
        connections = limits.bulkhead.per_backend
        engine = database_engine(database, settings.environment, connections)
        pool = checkpoint_pool(
            database, settings.environment, connections, limits.timeout.postgres_s
        )
        exchange = settings.token_exchange()
        credential: ManagedIdentityCredential | None = None
        proof: ClientProof
        match exchange:
            case FederatedExchange():
                credential = ManagedIdentityCredential(
                    client_id=str(exchange.managed_identity_client_id)
                )
                proof = FederatedProof(credential)
            case SecretExchange():
                proof = SecretProof(exchange.secret.get_secret_value())
        async with httpx2.AsyncClient() as http, pool:
            try:
                checkpoints = Checkpoints(pool)
                await checkpoints.ready()
                tokens = TokenClient(
                    OnBehalfOf(
                        http,
                        ExchangeTarget(
                            token_url=exchange.token_url,
                            client_id=exchange.client_id,
                            scope=exchange.scope,
                            timeout_s=limits.mcp_client.token_timeout_s,
                        ),
                        proof,
                    ),
                    refresh_before_s=limits.token.refresh_before_expiry_s,
                    kept=limits.mcp_client.tokens_kept,
                )
                gateway = McpGateway(
                    str(settings.mcp_url), tokens, timeout_s=limits.mcp_client.timeout_s
                )
                store = PostgresRunStore(engine)
                bus = Bus()
                cost = CostEstimate(load_pricing(settings.pricing_file), settings.usd_inr_rate)
                manager = RunManager(
                    Workings(
                        graph=build_graph(checkpoints.saver),
                        store=store,
                        bus=bus,
                        gateway=gateway,
                        model=chat_model(settings, limits),
                        cost=cost,
                        resources=ResourceCache(limits.health.cache_s),
                        limits=limits,
                        canary=settings.canary.get_secret_value(),
                        storage_ready=checkpoints.ensure,
                    )
                )

                async def ready() -> bool:
                    try:
                        async with engine.connect() as connection:
                            await connection.execute(_PING)
                    except (SQLAlchemyError, OSError):
                        return False
                    return await checkpoints.ready()

                yield (
                    Agent(
                        callers=Callers(token_verifier(settings.sign_in())),
                        gateway=gateway,
                        manager=manager,
                        store=store,
                        bus=bus,
                        cost=cost,
                        limits=limits,
                        demo_tools=settings.demo_tools,
                        route_timeout_s=limits.runs.route_timeout_s,
                    ),
                    ready,
                )
            finally:
                if credential is not None:
                    await credential.close()
                await engine.dispose()

    return opened


def create_app() -> FastAPI:
    """Entry point for the web server. Reads and checks every setting before serving."""
    settings = AgentSettings()  # pyright: ignore[reportCallIssue] -- fields come from the environment
    database = DatabaseSettings()  # pyright: ignore[reportCallIssue] -- fields come from the environment
    configure_logging(service=SERVICE_NAME, level=settings.log_level, json=settings.json_logs)
    adopt_library_logs()
    limits = load_limits(settings.limits_file)
    return build_app(production(settings, database, limits), limits, demo_tools=settings.demo_tools)

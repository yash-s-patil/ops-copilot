"""The web application: the MCP server at /mcp, and the probes."""

import ssl
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastmcp.server.auth import AuthProvider
from fastmcp.utilities.lifespan import combine_lifespans
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from starlette.exceptions import HTTPException as StarletteHTTPException

from opscp_common.limits import Limits, load_limits
from opscp_common.logging import configure_logging
from opscp_common.settings import LOCAL, DatabaseSettings, Environment, StorageSettings
from opscp_mcp.authz.tokens import sign_in_with
from opscp_mcp.container import OpenServices, Services, production_services
from opscp_mcp.rest.approvals import (
    APPROVALS_PATH,
    UNAUTHORISED,
    Access,
    NotSignedInError,
    invalid_request,
    unreadable,
)
from opscp_mcp.rest.approvals import router as approvals_router
from opscp_mcp.rest.body import SmallBodies
from opscp_mcp.rest.health import health_router
from opscp_mcp.rest.origin import NoBrowserOrigins
from opscp_mcp.server import SERVER_NAME, build_server
from opscp_mcp.settings import SERVICE_NAME, McpSettings
from opscp_mcp.telemetry.logs import adopt_framework_logs

MCP_PATH = "/mcp"
_SERVICES = "services"
_PROBE_CONNECTIONS = 1


def build_app(
    engine: AsyncEngine, open_services: OpenServices, limits: Limits, sign_in: AuthProvider
) -> FastAPI:
    """The application around an engine and a way to open the services. Disposes the engine.

    No browser page is a caller of this server, so a request that names an origin is refused.
    """
    opened: dict[str, Services] = {}

    @asynccontextmanager
    async def already_open() -> AsyncGenerator[Services]:
        yield opened[_SERVICES]

    mcp_app = build_server(already_open, limits, sign_in).http_app(
        path=MCP_PATH, stateless_http=True
    )
    mcp_app.router.redirect_slashes = False

    @asynccontextmanager
    async def run(app: FastAPI) -> AsyncGenerator[None]:
        """One set of services for the MCP endpoint and the routes beside it."""
        try:
            async with open_services() as services:
                opened[_SERVICES] = app.state.services = services
                yield
        finally:
            await engine.dispose()

    app = FastAPI(
        title=SERVER_NAME,
        lifespan=combine_lifespans(run, mcp_app.lifespan),
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(SmallBodies, under=APPROVALS_PATH, max_bytes=limits.approval.body_bytes_max)
    app.add_middleware(NoBrowserOrigins)
    app.add_exception_handler(NotSignedInError, _challenge(sign_in))
    app.add_exception_handler(StarletteHTTPException, unreadable)  # pyright: ignore[reportArgumentType] -- the handler takes the narrower error
    app.add_exception_handler(RequestValidationError, invalid_request)  # pyright: ignore[reportArgumentType] -- the handler takes the narrower error
    app.include_router(health_router(engine, database_timeout_s=limits.timeout.postgres_s))
    app.state.access = Access(sign_in, limits)
    app.include_router(approvals_router)
    app.mount("/", mcp_app)
    return app


def _challenge(sign_in: AuthProvider) -> Callable[[Request, Exception], Response]:
    """401, with where to learn how to sign in, as the MCP endpoint answers."""
    base = "" if sign_in.base_url is None else str(sign_in.base_url).rstrip("/")
    metadata = f"{base}/.well-known/oauth-protected-resource{MCP_PATH}"

    def unauthorised(_request: Request, _error: Exception) -> Response:
        return Response(
            status_code=UNAUTHORISED,
            headers={"WWW-Authenticate": f'Bearer resource_metadata="{metadata}"'},
        )

    return unauthorised


def database_engine(
    database: DatabaseSettings, environment: Environment, connections: int
) -> AsyncEngine:
    """Outside local development the connection is encrypted and the server's name checked.

    The pool holds a connection for every call that may be in flight, and one more for the
    readiness probe, which must not wait behind them.

    The default context trusts the system's certificate authorities. The driver's own
    "verify-full" does not: it looks for a root certificate in the user's home directory.
    """
    encrypted = {} if environment == LOCAL else {"ssl": ssl.create_default_context()}
    return create_async_engine(
        database.url(),
        hide_parameters=True,
        connect_args=encrypted,
        pool_size=connections + _PROBE_CONNECTIONS,
        max_overflow=0,
        pool_pre_ping=True,
    )


def create_app() -> FastAPI:
    """Entry point for the web server. Reads and checks every setting before serving."""
    settings = McpSettings()  # pyright: ignore[reportCallIssue] -- fields come from the environment
    database = DatabaseSettings()  # pyright: ignore[reportCallIssue] -- fields come from the environment
    storage = StorageSettings()  # pyright: ignore[reportCallIssue] -- fields come from the environment
    configure_logging(service=SERVICE_NAME, level=settings.log_level, json=settings.json_logs)
    adopt_framework_logs()
    limits = load_limits(settings.limits_file)
    engine = database_engine(database, settings.environment, limits.bulkhead.per_backend)
    sign_in = sign_in_with(settings.sign_in(), settings.mcp_public_url)
    return build_app(
        engine, production_services(settings, storage, limits, engine), limits, sign_in
    )

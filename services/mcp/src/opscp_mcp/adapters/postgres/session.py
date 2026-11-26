"""Row-level security: every read runs in a transaction that carries its caller's scope."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from opscp_mcp.ports.meridian import Scope

_SET_SCOPE = text(
    "SELECT set_config('app.user_oid', :user_oid, true), set_config('app.region', :region, true)"
)


@asynccontextmanager
async def read_as(engine: AsyncEngine, scope: Scope) -> AsyncGenerator[AsyncConnection]:
    """One read-only snapshot. The scope ends with it, so a pooled connection keeps none."""
    async with engine.connect() as connection:
        await connection.execution_options(
            isolation_level="REPEATABLE READ", postgresql_readonly=True
        )
        async with connection.begin():
            await connection.execute(
                _SET_SCOPE, {"user_oid": str(scope.user_oid), "region": scope.region}
            )
            yield connection

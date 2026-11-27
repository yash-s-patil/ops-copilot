"""How the adapters reach PostgreSQL: through the backend's guard, one transaction a call."""

import ssl
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from opscp_mcp.ports.meridian import Scope
from opscp_mcp.reliability.guard import Failure, Guard

SET_SCOPE = text(
    "SELECT set_config('app.user_oid', :user_oid, true), set_config('app.region', :region, true)"
)
# Connection exception, insufficient resources, and the server shutting down or starting.
_PASSING_SQLSTATES = ("08", "53", "57P")
# Invalid authorisation, and a database that does not exist.
_STANDING_SQLSTATES = ("28", "3D")

type Work[T] = Callable[[AsyncConnection], Awaitable[T]]


def classify(error: Exception) -> Failure | None:
    """What a failure says about the database. A wrong query says nothing: that is a defect.

    A certificate that does not verify is not retried: trying again would hide it.
    """
    if isinstance(error, ssl.SSLError):
        return Failure.STANDING
    if isinstance(error, OSError | PoolTimeoutError):
        return Failure.PASSING
    if not isinstance(error, DBAPIError):
        return None
    sqlstate = getattr(error.orig, "sqlstate", None)
    if error.connection_invalidated:
        return Failure.PASSING
    if not isinstance(sqlstate, str):
        return None
    if sqlstate.startswith(_PASSING_SQLSTATES):
        return Failure.PASSING
    return Failure.STANDING if sqlstate.startswith(_STANDING_SQLSTATES) else None


@asynccontextmanager
async def read_as(engine: AsyncEngine, scope: Scope) -> AsyncGenerator[AsyncConnection]:
    """One read-only snapshot. The scope ends with it, so a pooled connection keeps none."""
    async with engine.connect() as connection:
        await connection.execution_options(
            isolation_level="REPEATABLE READ", postgresql_readonly=True
        )
        async with connection.begin():
            await connection.execute(
                SET_SCOPE, {"user_oid": str(scope.user_oid), "region": scope.region}
            )
            yield connection


class Database:
    def __init__(self, engine: AsyncEngine, guard: Guard) -> None:
        self._engine = engine
        self._guard = guard

    async def read[T](self, scope: Scope, work: Work[T]) -> T:
        """Row-level security: the work sees only what its caller's scope allows."""

        async def attempt() -> T:
            async with read_as(self._engine, scope) as connection:
                return await work(connection)

        return await self._guard.run(attempt, retry=True)

    async def lookup[T](self, work: Work[T]) -> T:
        """A read that carries no caller's scope, for tables that are not divided by region.

        With no scope set, a table under row-level security shows it nothing.
        """

        async def attempt() -> T:
            async with self._engine.connect() as connection:
                await connection.execution_options(
                    isolation_level="REPEATABLE READ", postgresql_readonly=True
                )
                async with connection.begin():
                    return await work(connection)

        return await self._guard.run(attempt, retry=True)

    async def write[T](self, work: Work[T]) -> T:
        """One transaction, committed when the work returns. Never repeated."""

        async def attempt() -> T:
            async with self._engine.begin() as connection:
                return await work(connection)

        return await self._guard.run(attempt, retry=False)

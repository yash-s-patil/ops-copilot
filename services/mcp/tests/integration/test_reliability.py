"""The guard in front of PostgreSQL, against a real server and a real loss of connection."""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from opscp_common.errors import BackendTimeoutError, BackendUnavailableError
from opscp_common.limits import Limits
from opscp_mcp.adapters.postgres.audit import PostgresAuditTrail, broken_links
from opscp_mcp.adapters.postgres.orders import PostgresOrders
from opscp_mcp.adapters.postgres.session import Database, classify
from opscp_mcp.ports.controls import AuditEntry
from opscp_mcp.ports.meridian import Scope
from opscp_mcp.reliability.breaker import BreakerState
from opscp_mcp.services.scope import scope_of
from opscp_mcp_testing.guards import FAILURES, a_guard
from opscp_mcp_testing.identities import PRIYA
from opscp_mcp_testing.world import STUCK_ON_ADDRESS

from .database import WEST_ENGINEER, database_settings

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("world")]

NOWHERE = "postgresql+asyncpg://nobody:unused@127.0.0.1:1/nothing"
_DROP_OTHER_CONNECTIONS = text(
    "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity "
    "WHERE usename = 'mcp_app' AND application_name = :name"
)
_SLOW = text("SELECT pg_sleep(5)")
_BAD = text("SELECT * FROM core.no_such_table")
APPLICATION = "reliability-test"


@pytest_asyncio.fixture
async def engine() -> AsyncGenerator[AsyncEngine]:
    """A pool of its own, so that dropping its connections disturbs no other test."""
    engine = create_async_engine(
        database_settings("mcp_app").url(),
        connect_args={"server_settings": {"application_name": APPLICATION}},
    )
    try:
        yield engine
    finally:
        await engine.dispose()


def _scope() -> Scope:
    return scope_of(PRIYA)


async def _drop_connections(same_role: AsyncEngine) -> int:
    """A role may end its own sessions, so a second pool of the same role does it."""
    async with same_role.begin() as connection:
        return (
            await connection.execute(_DROP_OTHER_CONNECTIONS, {"name": APPLICATION})
        ).scalar_one()


async def test_a_read_survives_the_loss_of_its_connection(
    engine: AsyncEngine, mcp_app: AsyncEngine, limits: Limits
) -> None:
    guard = a_guard("postgres", classify=classify)
    orders = PostgresOrders(Database(engine, guard))
    before = await orders.get(_scope(), STUCK_ON_ADDRESS, recent_events=limits.events.recent)

    dropped = await _drop_connections(mcp_app)
    after = await orders.get(_scope(), STUCK_ON_ADDRESS, recent_events=limits.events.recent)

    assert dropped >= 1
    assert before is not None
    assert after == before
    assert guard.state is BreakerState.CLOSED


async def test_a_write_that_loses_its_connection_is_not_repeated(
    engine: AsyncEngine, mcp_app: AsyncEngine, migrator: AsyncEngine
) -> None:
    trail = PostgresAuditTrail(Database(engine, a_guard("postgres", classify=classify)))
    entry = AuditEntry(
        actor_oid=WEST_ENGINEER,
        actor_roles=("Support.L1",),
        actor_kind="user",
        target="get_order",
        action="call",
        args_redacted={},
        outcome="ok",
        duration_ms=1,
    )
    await trail.record(entry)
    async with migrator.connect() as connection:
        rows_before, _ = await broken_links(connection, limit=1)

    await _drop_connections(mcp_app)
    with pytest.raises(BackendUnavailableError):
        await trail.record(entry)
    await trail.record(entry)

    async with migrator.connect() as connection:
        rows_after, broken = await broken_links(connection, limit=1)
    assert (rows_after - rows_before, broken) == (1, [])


async def test_a_database_that_cannot_be_reached_opens_the_breaker(limits: Limits) -> None:
    unreachable = create_async_engine(NOWHERE)
    guard = a_guard("postgres", classify=classify)
    orders = PostgresOrders(Database(unreachable, guard))

    faults: list[BackendUnavailableError] = []
    try:
        for _ in range(4):
            with pytest.raises(BackendUnavailableError) as fault:
                await orders.get(_scope(), STUCK_ON_ADDRESS, recent_events=limits.events.recent)
            faults.append(fault.value)
    finally:
        await unreachable.dispose()

    assert guard.state is BreakerState.OPEN
    assert "127.0.0.1" not in "".join(fault.body().model_dump_json() for fault in faults)


async def test_a_query_that_takes_too_long_is_a_timeout(engine: AsyncEngine) -> None:
    database = Database(engine, a_guard("postgres", classify=classify, timeout_s=0.2))

    with pytest.raises(BackendTimeoutError):
        await database.read(_scope(), lambda connection: connection.execute(_SLOW))

    async with engine.connect() as connection:
        assert (await connection.execute(text("SELECT 1"))).scalar_one() == 1


async def test_a_wrong_query_is_a_defect_and_is_not_held_against_the_database(
    engine: AsyncEngine,
) -> None:
    guard = a_guard("postgres", classify=classify)
    database = Database(engine, guard)

    for _ in range(FAILURES * 2):
        with pytest.raises(Exception, match="no_such_table") as fault:
            await database.read(_scope(), lambda connection: connection.execute(_BAD))
        assert not isinstance(fault.value, BackendUnavailableError)

    assert guard.state is BreakerState.CLOSED


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://mcp_app:wrong-password@{host}:{port}/{name}",
        "postgresql+asyncpg://{user}:{password}@{host}:{port}/no_such_database",
    ],
    ids=["wrong password", "no such database"],
)
async def test_a_standing_refusal_by_the_database_is_not_retried_and_shows_as_unavailable(
    url: str, limits: Limits
) -> None:
    settings = database_settings("mcp_app")
    refused = create_async_engine(
        url.format(
            user=settings.user,
            password=settings.password.get_secret_value(),
            host=settings.host,
            port=settings.port,
            name=settings.name,
        )
    )
    guard = a_guard("postgres", classify=classify)
    orders = PostgresOrders(Database(refused, guard))

    try:
        for _ in range(FAILURES):
            with pytest.raises(BackendUnavailableError):
                await orders.get(_scope(), STUCK_ON_ADDRESS, recent_events=limits.events.recent)
    finally:
        await refused.dispose()

    assert guard.state is BreakerState.OPEN

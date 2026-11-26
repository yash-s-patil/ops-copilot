from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from opscp_mcp.adapters.postgres.customers import PostgresCustomers
from opscp_mcp.adapters.postgres.orders import PostgresOrders
from opscp_mcp.adapters.postgres.tickets import PostgresTickets
from opscp_mcp_testing.cases.backend import Backend
from opscp_mcp_testing.world import WORLD

from .database import (
    FIXTURE_PARAMETERS,
    FIXTURE_ROWS,
    SERVICE_ROOT,
    database_settings,
    guarded,
    start_from_empty_tables,
)
from .world_loader import load_world


def _alembic_config() -> Config:
    config = Config(SERVICE_ROOT / "alembic.ini")
    config.attributes["database_settings"] = database_settings("migrator")
    return config


@pytest.fixture(scope="session")
def migrated_database() -> Iterator[None]:
    config = _alembic_config()
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield
    command.downgrade(config, "base")


@asynccontextmanager
async def _engine(user: str) -> AsyncGenerator[AsyncEngine]:
    engine = create_async_engine(database_settings(user).url())
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def migrator(migrated_database: None) -> AsyncGenerator[AsyncEngine]:
    async with _engine("migrator") as engine:
        yield engine


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def fixture_rows(migrator: AsyncEngine) -> None:
    async with migrator.begin() as connection:
        await start_from_empty_tables(connection)
        for statement in FIXTURE_ROWS:
            await connection.execute(
                text(statement).bindparams(
                    **{k: v for k, v in FIXTURE_PARAMETERS.items() if f":{k}" in statement}
                )
            )


@pytest_asyncio.fixture(scope="session")
async def mcp_app(migrator: AsyncEngine) -> AsyncGenerator[AsyncEngine]:
    async with _engine("mcp_app") as engine:
        yield engine


@pytest_asyncio.fixture(scope="session")
async def payments_worker(migrator: AsyncEngine) -> AsyncGenerator[AsyncEngine]:
    async with _engine("payments_worker") as engine:
        yield engine


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def world(migrator: AsyncEngine) -> None:
    async with migrator.begin() as connection:
        await start_from_empty_tables(connection)
        await load_world(connection, WORLD)


@pytest.fixture
def backend(mcp_app: AsyncEngine, world: None) -> Backend:
    return Backend(
        customers=PostgresCustomers(guarded(mcp_app)),
        orders=PostgresOrders(guarded(mcp_app)),
        tickets=PostgresTickets(guarded(mcp_app)),
    )

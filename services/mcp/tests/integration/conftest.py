from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from .database import (
    FIXTURE_PARAMETERS,
    FIXTURE_ROWS,
    SERVICE_ROOT,
    SET_USER_CONTEXT,
    WEST_ENGINEER,
    database_settings,
)


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
        async with engine.begin() as connection:
            await connection.execute(SET_USER_CONTEXT, {"oid": str(WEST_ENGINEER), "region": "*"})
            for statement in FIXTURE_ROWS:
                await connection.execute(
                    text(statement).bindparams(
                        **{k: v for k, v in FIXTURE_PARAMETERS.items() if f":{k}" in statement}
                    )
                )
        yield engine


@pytest_asyncio.fixture(scope="session")
async def mcp_app(migrator: AsyncEngine) -> AsyncGenerator[AsyncEngine]:
    async with _engine("mcp_app") as engine:
        yield engine


@pytest_asyncio.fixture(scope="session")
async def payments_worker(migrator: AsyncEngine) -> AsyncGenerator[AsyncEngine]:
    async with _engine("payments_worker") as engine:
        yield engine

"""The local test database, reached as the migrator and as the payments worker."""

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from opscp_common.settings import DatabaseSettings

REPO_ROOT = Path(__file__).resolve().parents[4]
TEST_DATABASE = "meridian_test"
_LOCAL_HOSTS = ("127.0.0.1", "localhost")
_SCOPE_ALL = text("SELECT set_config('app.region', '*', true)")
_EMPTY = text(
    "TRUNCATE core.customers, core.orders, core.order_events, core.tickets, core.ticket_notes,"
    " core.refunds, control.pending_actions, control.approval_token_uses, control.outbox,"
    " control.rate_limits, control.chaos_flags RESTART IDENTITY"
)


def database_settings(user: str) -> DatabaseSettings:
    base = DatabaseSettings(_env_file=REPO_ROOT / ".env")  # pyright: ignore[reportCallIssue] -- fields come from the env file
    return base.model_copy(update={"user": user, "name": TEST_DATABASE})


@pytest.fixture(scope="session")
def schema() -> None:
    """The schema belongs to the MCP server; its migrations are applied, never undone, here."""
    config = Config(REPO_ROOT / "services" / "mcp" / "alembic.ini")
    config.attributes["database_settings"] = database_settings("migrator")
    command.upgrade(config, "head")


@pytest_asyncio.fixture(scope="session")
async def owner(schema: None) -> AsyncGenerator[AsyncEngine]:
    engine = create_async_engine(database_settings("migrator").url())
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def worker(schema: None) -> AsyncGenerator[AsyncEngine]:
    engine = create_async_engine(database_settings("payments_worker").url())
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def empty_tables(owner: AsyncEngine) -> None:
    async with owner.begin() as connection:
        database = await connection.scalar(text("SELECT current_database()"))
        host = database_settings("migrator").host
        if database != TEST_DATABASE or host not in _LOCAL_HOSTS:
            raise RuntimeError(f"refusing to empty the tables of {database} on {host}")
        await connection.execute(_SCOPE_ALL)
        await connection.execute(_EMPTY)

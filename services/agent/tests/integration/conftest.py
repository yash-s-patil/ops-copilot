"""The agent's own test database, reached as the migrator and as the agent."""

from collections.abc import AsyncGenerator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from opscp_common.settings import DatabaseSettings

REPO_ROOT = Path(__file__).resolve().parents[4]
SERVICE_ROOT = Path(__file__).resolve().parents[2]
TEST_DATABASE = "copilot_test"
AGENT_ROLE = "agent_app"
_LOCAL_HOSTS = ("127.0.0.1", "localhost")
_EMPTY = text(
    "TRUNCATE copilot.runs, copilot.run_events, copilot.run_actions, copilot.llm_calls,"
    " copilot.guardrail_events"
)


class _AgentRole(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", frozen=True, hide_input_in_errors=True)

    agent_database_password: SecretStr


def database_settings(user: str, database: str = TEST_DATABASE) -> DatabaseSettings:
    """As the migrator, or as the agent with the password that is the agent's alone."""
    base = DatabaseSettings(_env_file=REPO_ROOT / ".env")  # pyright: ignore[reportCallIssue] -- fields come from the env file
    changed: dict[str, object] = {"user": user, "name": database}
    if user == AGENT_ROLE:
        own = _AgentRole(_env_file=REPO_ROOT / ".env")  # pyright: ignore[reportCallIssue] -- fields come from the env file
        changed["password"] = own.agent_database_password
    return base.model_copy(update=changed)


@pytest.fixture(scope="session")
def migrated_database() -> Iterator[None]:
    """Applied, undone and applied again, so that the way back is tested with the way there."""
    config = Config(SERVICE_ROOT / "alembic.ini")
    config.attributes["database_settings"] = database_settings("migrator")
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield
    command.downgrade(config, "base")


@pytest_asyncio.fixture(scope="session")
async def owner(migrated_database: None) -> AsyncGenerator[AsyncEngine]:
    engine = create_async_engine(database_settings("migrator").url())
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def agent_app(migrated_database: None) -> AsyncGenerator[AsyncEngine]:
    engine = create_async_engine(database_settings("agent_app").url())
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def empty_tables(owner: AsyncEngine) -> None:
    async with owner.begin() as connection:
        database = await connection.scalar(text("SELECT current_database()"))
        host = database_settings("migrator").host
        if database != TEST_DATABASE or host not in _LOCAL_HOSTS:
            raise RuntimeError(f"refusing to empty the tables of {database} on {host}")
        await connection.execute(_EMPTY)

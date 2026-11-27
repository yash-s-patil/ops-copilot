"""Where LangGraph saves each step of a run: tables of its own, in a schema of their own.

The checkpointer creates and manages its tables. It is asked to when they are first
needed and not at start-up, so the service can start before its database has been
migrated and becomes ready once it has.
"""

import asyncio

import structlog
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection, Error
from psycopg.conninfo import make_conninfo
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from opscp_common.errors import BackendUnavailableError
from opscp_common.settings import LOCAL, DatabaseSettings, Environment

SCHEMA = "langgraph"
_LOG = structlog.get_logger(__name__)

type Pool = AsyncConnectionPool[AsyncConnection[DictRow]]


def checkpoint_pool(
    database: DatabaseSettings, environment: Environment, connections: int, timeout_s: int
) -> Pool:
    """Outside local development the connection is encrypted and the server's name checked."""
    encrypted = {} if environment == LOCAL else {"sslmode": "verify-full", "sslrootcert": "system"}
    conninfo = make_conninfo(
        host=database.host,
        port=database.port,
        dbname=database.name,
        user=database.user,
        password=database.password.get_secret_value(),
        options=f"-c search_path={SCHEMA}",
        **encrypted,
    )
    return AsyncConnectionPool(
        conninfo,
        open=False,
        min_size=1,
        max_size=connections,
        timeout=timeout_s,
        # The three settings the checkpointer asks of its connections.
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )


class Checkpoints:
    def __init__(self, pool: Pool) -> None:
        self.saver = AsyncPostgresSaver(pool)
        self._set_up = False
        self._setting_up = asyncio.Lock()

    async def ready(self) -> bool:
        """Whether the tables exist. Creates them the first time the schema is there."""
        if self._set_up:
            return True
        async with self._setting_up:
            if not self._set_up:
                try:
                    await self.saver.setup()
                except Error as error:
                    _LOG.warning("checkpoints_not_ready", error=type(error).__name__)
                    return False
                self._set_up = True
        return True

    async def ensure(self) -> None:
        if not await self.ready():
            raise BackendUnavailableError("The assistant is starting up. Try again shortly.")

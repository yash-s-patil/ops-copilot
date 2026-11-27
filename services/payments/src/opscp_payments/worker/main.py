"""The payments worker: every few seconds, work on the outbox rows that are due.

It has no HTTP ingress. It talks to PostgreSQL as `payments_worker` and to the payment
service with its own token, and to nothing else.
"""

import asyncio
import logging
import signal
import ssl
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Protocol

import httpx2
import structlog
from azure.identity.aio import ManagedIdentityCredential
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from opscp_common.limits import Limits, load_limits
from opscp_common.logging import configure_logging
from opscp_common.settings import LOCAL, DatabaseSettings, Environment
from opscp_payments.adapters.postgres import PostgresOutbox
from opscp_payments.adapters.sim_client import HttpPaymentProvider
from opscp_payments.adapters.tokens import DevTokens, ManagedIdentityTokens
from opscp_payments.ports.payments import AccessTokens
from opscp_payments.settings import WORKER_NAME, ManagedIdentity, WorkerSettings
from opscp_payments.worker.outbox import Handoffs

_LOG = structlog.get_logger(__name__)
# One for the row being worked on, one to look for the next.
_CONNECTIONS = 2
_HTTP_CLIENT = "httpx2"


def utc_now() -> datetime:
    return datetime.now(UTC)


class Polled(Protocol):
    async def poll(self) -> int: ...


async def run(
    handoffs: Polled, poll_interval_s: float, stop: asyncio.Event, *, full_batch: int
) -> None:
    """Polls until told to stop. A poll that fails is logged and the next one still runs.

    After a poll that held a full batch there is probably more waiting, so the next poll
    follows at once.
    """
    while not stop.is_set():
        held = 0
        try:
            held = await handoffs.poll()
        except Exception as error:  # noqa: BLE001 -- an outage must not end the worker
            _LOG.error("poll_failed", error=type(error).__name__)
        if held >= full_batch:
            continue
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), poll_interval_s)


def database_engine(
    database: DatabaseSettings, environment: Environment, statement_timeout_s: int
) -> AsyncEngine:
    """Outside local development the connection is encrypted and the server's name checked."""
    encrypted = {} if environment == LOCAL else {"ssl": ssl.create_default_context()}
    return create_async_engine(
        database.url(),
        hide_parameters=True,
        connect_args={"command_timeout": statement_timeout_s, **encrypted},
        pool_size=_CONNECTIONS,
        max_overflow=0,
        pool_pre_ping=True,
    )


@asynccontextmanager
async def _tokens(settings: WorkerSettings) -> AsyncGenerator[AccessTokens]:
    identity = settings.identity()
    if isinstance(identity, ManagedIdentity):
        async with ManagedIdentityCredential(client_id=str(identity.client_id)) as credential:
            yield ManagedIdentityTokens(credential, identity.scope)
    else:
        yield DevTokens(identity)


async def serve(settings: WorkerSettings, database: DatabaseSettings, limits: Limits) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for wanted in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(wanted, stop.set)
    engine = database_engine(database, settings.environment, limits.timeout.postgres_s)
    async with AsyncExitStack() as stack:
        stack.push_async_callback(engine.dispose)
        http = await stack.enter_async_context(
            httpx2.AsyncClient(
                base_url=str(settings.payments_url),
                timeout=limits.payments.http_timeout_s,
                trust_env=False,
            )
        )
        tokens = await stack.enter_async_context(_tokens(settings))
        provider = HttpPaymentProvider(http, tokens, timeout_s=limits.payments.http_timeout_s)
        handoffs = Handoffs(
            PostgresOutbox(engine),
            provider,
            limits.payments,
            utc_now,
            chaos_enabled=settings.chaos_enabled,
        )
        _LOG.info("worker_started", poll_interval_s=limits.payments.poll_interval_s)
        await run(
            handoffs,
            limits.payments.poll_interval_s,
            stop,
            full_batch=limits.payments.claim_batch,
        )
        _LOG.info("worker_stopped")


def main() -> None:
    """Entry point. Reads and checks every setting before the first poll."""
    settings = WorkerSettings()  # pyright: ignore[reportCallIssue] -- fields come from the environment
    database = DatabaseSettings()  # pyright: ignore[reportCallIssue] -- fields come from the environment
    configure_logging(service=WORKER_NAME, level=settings.log_level, json=settings.json_logs)
    # The client logs every request with its address, which names a refund's reference.
    logging.getLogger(_HTTP_CLIENT).setLevel(logging.WARNING)
    asyncio.run(serve(settings, database, load_limits(settings.limits_file)))

"""The worker's loop: it keeps polling through failures and stops when told to."""

import asyncio
import ssl

import pytest
import structlog
from pydantic import SecretStr

from opscp_common.settings import DatabaseSettings, Environment
from opscp_payments.worker import main
from opscp_payments.worker.main import database_engine, run, utc_now


class _Handoffs:
    def __init__(
        self, stop: asyncio.Event, *, stop_after: int, fail_on: int = 0, held: int = 0
    ) -> None:
        self.polls = 0
        self._stop = stop
        self._stop_after = stop_after
        self._fail_on = fail_on
        self._held = held

    async def poll(self) -> int:
        self.polls += 1
        if self.polls == self._stop_after:
            self._stop.set()
        if self.polls == self._fail_on:
            raise ConnectionError("postgres://payments_worker:secret@db/meridian is down")
        return self._held


async def test_the_worker_polls_until_it_is_told_to_stop() -> None:
    stop = asyncio.Event()
    handoffs = _Handoffs(stop, stop_after=3)

    await asyncio.wait_for(run(handoffs, 0.001, stop, full_batch=10), timeout=5)

    assert handoffs.polls == 3


async def test_a_poll_that_fails_is_logged_by_kind_and_the_next_one_still_runs() -> None:
    stop = asyncio.Event()
    handoffs = _Handoffs(stop, stop_after=3, fail_on=1)

    with structlog.testing.capture_logs() as logs:
        await asyncio.wait_for(run(handoffs, 0.001, stop, full_batch=10), timeout=5)

    assert handoffs.polls == 3
    assert logs == [{"event": "poll_failed", "error": "ConnectionError", "log_level": "error"}]


async def test_a_stop_ends_the_wait_between_polls_at_once() -> None:
    stop = asyncio.Event()
    handoffs = _Handoffs(stop, stop_after=1)

    await asyncio.wait_for(run(handoffs, 3600, stop, full_batch=10), timeout=5)

    assert handoffs.polls == 1


async def test_after_a_full_batch_the_next_poll_follows_without_waiting() -> None:
    stop = asyncio.Event()
    handoffs = _Handoffs(stop, stop_after=4, held=10)

    await asyncio.wait_for(run(handoffs, 3600, stop, full_batch=10), timeout=5)

    assert handoffs.polls == 4


async def test_a_worker_told_to_stop_before_it_starts_does_nothing() -> None:
    stop = asyncio.Event()
    stop.set()
    handoffs = _Handoffs(stop, stop_after=99)

    await run(handoffs, 3600, stop, full_batch=10)

    assert handoffs.polls == 0


def _database() -> DatabaseSettings:
    return DatabaseSettings(
        host="db.internal", name="meridian", user="payments_worker", password=SecretStr("s3cret")
    )


@pytest.mark.parametrize("environment", ["demo", "prod"])
def test_the_database_connection_is_verified_tls_outside_local_and_every_statement_is_bounded(
    environment: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    made: list[tuple[dict[str, object], dict[str, object]]] = []

    def record(_url: object, *, connect_args: dict[str, object], **rest: object) -> object:
        made.append((connect_args, rest))
        return object()

    monkeypatch.setattr(main, "create_async_engine", record)

    database_engine(_database(), environment, 5)
    database_engine(_database(), "local", 5)

    (deployed, options), (local, _) = made
    context = deployed["ssl"]
    assert isinstance(context, ssl.SSLContext)
    assert (context.verify_mode, context.check_hostname) == (ssl.CERT_REQUIRED, True)
    assert (deployed["command_timeout"], local) == (5, {"command_timeout": 5})
    assert options == {
        "hide_parameters": True,
        "pool_size": 2,
        "max_overflow": 0,
        "pool_pre_ping": True,
    }
    assert utc_now().tzinfo is not None

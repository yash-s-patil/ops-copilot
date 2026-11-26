"""What a guard does with a call to a backend that works, fails, hangs or is abandoned."""

import asyncio
import contextlib

import pytest
import structlog

from opscp_common.errors import BackendTimeoutError, BackendUnavailableError
from opscp_mcp.reliability.breaker import BreakerState
from opscp_mcp.reliability.deadline import within
from opscp_mcp.reliability.guard import Guard, backoff_s
from opscp_mcp_testing.guards import (
    FAILURES,
    HALF_OPEN_AFTER_S,
    RETRIES,
    SettableClock,
    a_guard,
)


class _Backend:
    """Fails with each given error in turn, then answers."""

    def __init__(self, *failures: Exception) -> None:
        self._failures = list(failures)
        self.calls = 0

    async def __call__(self) -> str:
        self.calls += 1
        if self._failures:
            raise self._failures.pop(0)
        return "answer"


def _down() -> _Backend:
    return _Backend(*(ConnectionError("refused") for _ in range(1000)))


async def test_a_call_that_works_is_made_once() -> None:
    backend = _Backend()

    assert await a_guard("test").run(backend, retry=True) == "answer"
    assert backend.calls == 1


async def test_a_read_that_fails_in_passing_is_tried_again_after_a_pause() -> None:
    pauses: list[float] = []

    async def pause(seconds: float) -> None:
        pauses.append(seconds)

    backend = _Backend(ConnectionError("reset"), ConnectionError("reset"))

    assert await a_guard("test", pause=pause).run(backend, retry=True) == "answer"
    assert backend.calls == RETRIES + 1
    assert len(pauses) == RETRIES
    assert 0 <= pauses[0] <= 0.1
    assert 0 <= pauses[1] <= 0.2


async def test_a_read_is_given_up_after_two_more_tries() -> None:
    backend = _down()

    with pytest.raises(BackendUnavailableError) as fault:
        await a_guard("test").run(backend, retry=True)

    body = fault.value.body()
    assert backend.calls == RETRIES + 1
    assert (body.retryable, body.details) == (True, {})
    assert "refused" not in body.message


async def test_a_write_is_never_tried_again() -> None:
    backend = _Backend(ConnectionError("reset"))

    with pytest.raises(BackendUnavailableError):
        await a_guard("test").run(backend, retry=False)

    assert backend.calls == 1


async def test_a_failure_that_will_not_pass_is_raised_as_it_is_and_not_counted() -> None:
    guard = a_guard("test")

    for _ in range(FAILURES * 2):
        with pytest.raises(KeyError):
            await guard.run(_Backend(KeyError("a defect")), retry=True)

    assert guard.state is BreakerState.CLOSED


async def test_an_attempt_that_takes_too_long_is_a_timeout_and_is_not_tried_again() -> None:
    calls = 0

    async def hangs() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(3600)
        return "never"

    with pytest.raises(BackendTimeoutError) as fault:
        await a_guard("test", timeout_s=0.01).run(hangs, retry=True)

    assert calls == 1
    assert fault.value.body().retryable


async def test_repeated_failures_open_the_breaker_and_the_backend_is_left_alone() -> None:
    guard, backend = a_guard("test"), _down()

    with structlog.testing.capture_logs() as logs:
        for _ in range(10):
            with pytest.raises(BackendUnavailableError):
                await guard.run(backend, retry=True)

    assert guard.state is BreakerState.OPEN
    assert backend.calls == FAILURES
    assert [line["event"] for line in logs] == ["backend_failed"] * FAILURES + ["breaker_opened"]
    assert {line["backend"] for line in logs} == {"test"}
    assert "refused" not in str(logs)


async def test_timeouts_count_towards_opening_the_breaker() -> None:
    guard = a_guard("test", timeout_s=0.001)

    for _ in range(FAILURES):
        with pytest.raises(BackendTimeoutError):
            await guard.run(lambda: asyncio.sleep(3600), retry=True)

    assert guard.state is BreakerState.OPEN


async def test_a_retry_stops_as_soon_as_the_breaker_opens() -> None:
    guard, backend = a_guard("test"), _down()
    for _ in range(FAILURES - 1):
        with pytest.raises(BackendUnavailableError):
            await guard.run(backend, retry=False)

    with pytest.raises(BackendUnavailableError):
        await guard.run(backend, retry=True)

    assert backend.calls == FAILURES


async def test_after_the_pause_one_probe_goes_through_and_success_closes_the_breaker() -> None:
    clock = SettableClock()
    guard, backend = a_guard("test", clock=clock), _down()
    for _ in range(FAILURES):
        with pytest.raises(BackendUnavailableError):
            await guard.run(backend, retry=False)
    clock.now += HALF_OPEN_AFTER_S
    recovered = _Backend()

    with structlog.testing.capture_logs() as logs:
        answers = [await guard.run(recovered, retry=True) for _ in range(3)]

    assert answers == ["answer"] * 3
    assert guard.state is BreakerState.CLOSED
    assert logs == [{"event": "breaker_closed", "backend": "test", "log_level": "info"}]


async def test_a_probe_that_fails_keeps_the_breaker_open_without_a_second_try() -> None:
    clock = SettableClock()
    guard, backend = a_guard("test", clock=clock), _down()
    for _ in range(FAILURES):
        with pytest.raises(BackendUnavailableError):
            await guard.run(backend, retry=False)
    clock.now += HALF_OPEN_AFTER_S

    with pytest.raises(BackendUnavailableError):
        await guard.run(backend, retry=True)

    assert backend.calls == FAILURES + 1
    assert guard.state is BreakerState.OPEN


async def test_while_a_probe_is_out_other_calls_are_refused_at_once() -> None:
    clock = SettableClock()
    guard, backend = a_guard("test", clock=clock), _down()
    for _ in range(FAILURES):
        with pytest.raises(BackendUnavailableError):
            await guard.run(backend, retry=False)
    clock.now += HALF_OPEN_AFTER_S
    release = asyncio.Event()

    async def slow_probe() -> str:
        await release.wait()
        return "answer"

    probe = asyncio.create_task(guard.run(slow_probe, retry=True))
    await asyncio.sleep(0)
    with pytest.raises(BackendUnavailableError):
        await guard.run(_Backend(), retry=True)
    release.set()

    assert await probe == "answer"
    assert guard.state is BreakerState.CLOSED


async def test_an_abandoned_probe_does_not_leave_the_breaker_stuck() -> None:
    clock = SettableClock()
    guard, backend = a_guard("test", clock=clock), _down()
    for _ in range(FAILURES):
        with pytest.raises(BackendUnavailableError):
            await guard.run(backend, retry=False)
    clock.now += HALF_OPEN_AFTER_S

    probe = asyncio.create_task(guard.run(lambda: asyncio.sleep(3600), retry=True))
    await asyncio.sleep(0)
    probe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await probe

    assert await guard.run(_Backend(), retry=True) == "answer"
    assert guard.state is BreakerState.CLOSED


async def test_no_more_than_the_allowed_number_of_calls_are_in_flight() -> None:
    guard = a_guard("test", concurrent=3)
    in_flight = 0
    most = 0

    async def slow() -> None:
        nonlocal in_flight, most
        in_flight += 1
        most = max(most, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1

    await asyncio.gather(*(guard.run(slow, retry=True) for _ in range(12)))

    assert most == 3


async def test_a_failed_call_gives_its_place_to_the_next() -> None:
    guard = a_guard("test", concurrent=1)

    with pytest.raises(BackendUnavailableError):
        await guard.run(_down(), retry=False)

    assert await asyncio.wait_for(guard.run(_Backend(), retry=True), timeout=1) == "answer"


@pytest.mark.parametrize(
    ("attempt", "jitter", "expected"),
    [(0, 1.0, 0.1), (1, 1.0, 0.2), (2, 1.0, 0.4), (1, 0.5, 0.1), (3, 0.0, 0.0)],
)
def test_the_pause_before_a_retry_is_a_random_part_of_a_doubling_ceiling(
    attempt: int, jitter: float, expected: float
) -> None:
    assert backoff_s(attempt, 100, lambda: jitter) == pytest.approx(expected)


async def _open(guard: Guard) -> None:
    for _ in range(FAILURES):
        with pytest.raises(BackendUnavailableError):
            await guard.run(_down(), retry=False)


async def test_a_call_cut_off_by_its_callers_time_limit_counts_against_the_backend() -> None:
    guard = a_guard("test", timeout_s=3600)

    for _ in range(FAILURES):
        with pytest.raises(BackendTimeoutError):
            await within(0.01, guard.run(lambda: asyncio.sleep(3600), retry=True))

    assert guard.state is BreakerState.OPEN


async def test_a_call_whose_client_went_away_does_not_count_against_the_backend() -> None:
    guard = a_guard("test")

    for _ in range(FAILURES * 2):
        gone = asyncio.create_task(within(3600, guard.run(lambda: asyncio.sleep(3600), retry=True)))
        await asyncio.sleep(0)
        gone.cancel()
        with pytest.raises(asyncio.CancelledError):
            await gone

    assert guard.state is BreakerState.CLOSED


async def test_a_probe_that_hangs_until_its_callers_limit_reopens_the_breaker() -> None:
    clock = SettableClock()
    guard = a_guard("test", clock=clock, timeout_s=3600)
    await _open(guard)
    clock.now += HALF_OPEN_AFTER_S

    with pytest.raises(BackendTimeoutError):
        await within(0.01, guard.run(lambda: asyncio.sleep(3600), retry=True))

    assert guard.state is BreakerState.OPEN


@pytest.mark.parametrize("late", [None, KeyError("a defect"), ConnectionError("refused")])
async def test_a_call_let_through_before_the_breaker_opened_does_not_change_it_when_it_ends(
    late: Exception | None,
) -> None:
    guard = a_guard("test")
    release = asyncio.Event()

    async def in_flight() -> str:
        await release.wait()
        if late is not None:
            raise late
        return "answer"

    early = asyncio.create_task(guard.run(in_flight, retry=False))
    await asyncio.sleep(0)
    await _open(guard)
    release.set()
    with contextlib.suppress(KeyError, BackendUnavailableError):
        await early

    assert guard.state is BreakerState.OPEN
    with pytest.raises(BackendUnavailableError):
        await guard.run(_Backend(), retry=True)


async def test_time_given_to_work_is_the_shorter_of_its_own_and_its_callers() -> None:
    async def inner() -> None:
        await within(3600, asyncio.sleep(3600))

    with pytest.raises(BackendTimeoutError) as fault:
        await within(0.01, inner())

    assert fault.value.body().message == "The request took too long. Try again."


async def test_a_standing_failure_is_not_tried_again_but_does_open_the_breaker() -> None:
    guard = a_guard("test")
    backend = _Backend(*(PermissionError("password refused") for _ in range(100)))

    for _ in range(FAILURES + 2):
        with pytest.raises(BackendUnavailableError):
            await guard.run(backend, retry=True)

    assert backend.calls == FAILURES
    assert guard.state is BreakerState.OPEN

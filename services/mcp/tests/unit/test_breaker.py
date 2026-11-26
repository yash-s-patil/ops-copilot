from opscp_mcp.reliability.breaker import Admission, BreakerState, CircuitBreaker
from opscp_mcp_testing.guards import FAILURES, HALF_OPEN_AFTER_S, WINDOW_S, SettableClock

CALL, PROBE, REFUSED = Admission.CALL, Admission.PROBE, Admission.REFUSED


def _breaker(clock: SettableClock) -> CircuitBreaker:
    return CircuitBreaker(
        failures=FAILURES, window_s=WINDOW_S, half_open_after_s=HALF_OPEN_AFTER_S, clock=clock
    )


def _fail(breaker: CircuitBreaker, times: int) -> list[bool]:
    return [breaker.failed(CALL) for _ in range(times)]


def _open(clock: SettableClock) -> CircuitBreaker:
    breaker = _breaker(clock)
    _fail(breaker, FAILURES)
    return breaker


def test_a_new_breaker_lets_calls_through() -> None:
    breaker = _breaker(SettableClock())

    assert (breaker.state, breaker.admit()) == (BreakerState.CLOSED, CALL)


def test_it_opens_on_the_fifth_failure_and_refuses_calls() -> None:
    breaker = _breaker(SettableClock())

    assert _fail(breaker, FAILURES) == [False, False, False, False, True]
    assert (breaker.state, breaker.admit()) == (BreakerState.OPEN, REFUSED)


def test_failures_spread_over_more_than_the_window_do_not_open_it() -> None:
    clock = SettableClock()
    breaker = _breaker(clock)

    for _ in range(FAILURES * 3):
        breaker.failed(CALL)
        clock.now += WINDOW_S / (FAILURES - 1)

    assert breaker.state is BreakerState.CLOSED


def test_a_failure_exactly_one_window_old_no_longer_counts() -> None:
    clock = SettableClock()
    breaker = _breaker(clock)
    breaker.failed(CALL)
    clock.now += WINDOW_S

    assert _fail(breaker, FAILURES - 1) == [False] * (FAILURES - 1)
    assert breaker.failed(CALL) is True


def test_successes_between_failures_do_not_reset_the_count() -> None:
    breaker = _breaker(SettableClock())

    for _ in range(FAILURES - 1):
        breaker.failed(CALL)
        assert breaker.succeeded(CALL) is False

    assert breaker.failed(CALL) is True


def test_it_stays_open_until_the_pause_has_passed() -> None:
    clock = SettableClock()
    breaker = _open(clock)

    clock.now += HALF_OPEN_AFTER_S - 0.001
    still_open = (breaker.state, breaker.admit())
    clock.now += 0.001

    assert still_open == (BreakerState.OPEN, REFUSED)
    assert breaker.state is BreakerState.HALF_OPEN


def test_after_the_pause_exactly_one_call_is_let_through_as_the_probe() -> None:
    clock = SettableClock()
    breaker = _open(clock)
    clock.now += HALF_OPEN_AFTER_S

    assert [breaker.admit() for _ in range(4)] == [PROBE, REFUSED, REFUSED, REFUSED]


def test_a_probe_that_succeeds_closes_it_with_a_clean_count() -> None:
    clock = SettableClock()
    breaker = _open(clock)
    clock.now += HALF_OPEN_AFTER_S

    assert breaker.succeeded(breaker.admit()) is True
    assert (breaker.state, breaker.admit()) == (BreakerState.CLOSED, CALL)
    assert _fail(breaker, FAILURES - 1) == [False] * (FAILURES - 1)


def test_a_probe_that_fails_starts_the_pause_again() -> None:
    clock = SettableClock()
    breaker = _open(clock)
    clock.now += HALF_OPEN_AFTER_S

    assert breaker.failed(breaker.admit()) is False
    assert (breaker.state, breaker.admit()) == (BreakerState.OPEN, REFUSED)
    clock.now += HALF_OPEN_AFTER_S
    assert breaker.admit() is PROBE


def test_a_probe_that_was_abandoned_lets_another_take_its_place() -> None:
    clock = SettableClock()
    breaker = _open(clock)
    clock.now += HALF_OPEN_AFTER_S

    breaker.abandoned(breaker.admit())

    assert (breaker.state, breaker.admit(), breaker.admit()) == (
        BreakerState.HALF_OPEN,
        PROBE,
        REFUSED,
    )


def test_only_the_probe_decides_once_the_breaker_has_opened() -> None:
    clock = SettableClock()
    breaker = _open(clock)

    late = (breaker.succeeded(CALL), breaker.failed(CALL))
    breaker.abandoned(CALL)
    clock.now += HALF_OPEN_AFTER_S
    probe = breaker.admit()
    during_the_probe = (breaker.succeeded(CALL), breaker.failed(CALL))
    breaker.abandoned(CALL)

    assert late == during_the_probe == (False, False)
    assert (probe, breaker.admit()) == (PROBE, REFUSED)
    assert breaker.state is BreakerState.HALF_OPEN


def test_a_late_failure_does_not_put_off_the_probe() -> None:
    clock = SettableClock()
    breaker = _open(clock)
    clock.now += HALF_OPEN_AFTER_S - 1

    breaker.failed(CALL)
    clock.now += 1

    assert breaker.admit() is PROBE


def test_failures_from_before_it_opened_are_forgotten_when_it_closes() -> None:
    clock = SettableClock()
    breaker = _open(clock)
    clock.now += HALF_OPEN_AFTER_S
    breaker.succeeded(breaker.admit())

    assert _fail(breaker, FAILURES) == [False, False, False, False, True]

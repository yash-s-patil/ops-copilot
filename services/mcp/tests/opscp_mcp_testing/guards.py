"""Guards for tests: the real thing, with limits a test can choose."""

from collections.abc import Awaitable, Callable

from opscp_mcp.reliability.breaker import CircuitBreaker
from opscp_mcp.reliability.guard import Classify, Failure, Guard, GuardLimits

FAILURES = 5
WINDOW_S = 30
HALF_OPEN_AFTER_S = 20
RETRIES = 2


class SettableClock:
    """A clock that moves only when a test moves it."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def lost_connections_pass(error: Exception) -> Failure | None:
    if isinstance(error, ConnectionError):
        return Failure.PASSING
    return Failure.STANDING if isinstance(error, PermissionError) else None


async def no_pause(_seconds: float) -> None:
    return None


def a_guard(
    backend: str = "test",
    *,
    classify: Classify = lost_connections_pass,
    clock: Callable[[], float] | None = None,
    timeout_s: float = 5,
    concurrent: int = 20,
    pause: Callable[[float], Awaitable[None]] = no_pause,
) -> Guard:
    breaker = CircuitBreaker(
        failures=FAILURES,
        window_s=WINDOW_S,
        half_open_after_s=HALF_OPEN_AFTER_S,
        **({} if clock is None else {"clock": clock}),
    )
    return Guard(
        backend,
        GuardLimits(timeout_s=timeout_s, retries=RETRIES, retry_base_ms=100, concurrent=concurrent),
        breaker=breaker,
        classify=classify,
        pause=pause,
    )

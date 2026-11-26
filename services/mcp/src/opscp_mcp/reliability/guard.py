"""What stands between the server and one backend.

Every call to a backend goes through its guard, which applies, from the outside in: a cap on
calls in flight, so one slow backend cannot hold every worker; the circuit breaker; a few
retries for reads that failed in a way that may pass; and a time limit on each attempt. A
call that is refused or gives up is reported with one of two errors the caller may retry.
"""

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

import structlog

from opscp_common.errors import BackendTimeoutError, BackendUnavailableError
from opscp_mcp.reliability import deadline
from opscp_mcp.reliability.breaker import Admission, BreakerState, CircuitBreaker
from opscp_mcp.reliability.deadline import TOO_SLOW

UNAVAILABLE = "A system this needs is unavailable. Try again shortly."

_LOG = structlog.get_logger(__name__)
_MS_PER_S = 1000


class Failure(StrEnum):
    """What a failure says about a backend. A failure that says nothing is a defect of ours."""

    PASSING = "passing"
    STANDING = "standing"


type Classify = Callable[[Exception], Failure | None]


class _MayPassError(BackendUnavailableError):
    """Raised for a failure worth another attempt, such as a lost connection."""


def backoff_s(attempt: int, base_ms: int, jitter: Callable[[], float] = random.random) -> float:
    """Full jitter: anywhere up to a ceiling that doubles with each attempt already made."""
    return jitter() * base_ms * 2**attempt / _MS_PER_S


@dataclass(frozen=True, slots=True, kw_only=True)
class GuardLimits:
    timeout_s: float
    retries: int
    retry_base_ms: int
    concurrent: int


class Guard:
    def __init__(
        self,
        backend: str,
        limits: GuardLimits,
        *,
        breaker: CircuitBreaker,
        classify: Classify,
        pause: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.backend = backend
        self._breaker = breaker
        self._classify = classify
        self._timeout_s = limits.timeout_s
        self._retries = limits.retries
        self._retry_base_ms = limits.retry_base_ms
        self._in_flight = asyncio.Semaphore(limits.concurrent)
        self._pause = pause

    @property
    def state(self) -> BreakerState:
        return self._breaker.state

    async def run[T](self, operation: Callable[[], Awaitable[T]], *, retry: bool) -> T:
        """Runs the operation, again if `retry` and it failed in a way that may pass.

        Only an operation that changes nothing may be retried: a failure does not say
        whether the backend acted on the request before the connection was lost. A standing
        failure, such as a refused credential, counts against the backend but is not retried.
        """
        attempts = 1 + (self._retries if retry else 0)
        failed = 0
        async with self._in_flight:
            while True:
                try:
                    return await self._attempt(operation)
                except _MayPassError:
                    failed += 1
                    if failed == attempts or self._breaker.state is not BreakerState.CLOSED:
                        raise
                await self._pause(backoff_s(failed - 1, self._retry_base_ms))

    async def _attempt[T](self, operation: Callable[[], Awaitable[T]]) -> T:
        admitted = self._breaker.admit()
        if admitted is Admission.REFUSED:
            raise BackendUnavailableError(UNAVAILABLE)
        try:
            async with asyncio.timeout(self._timeout_s):
                result = await operation()
        except TimeoutError as error:
            self._failed(admitted, "TimeoutError")
            raise BackendTimeoutError(TOO_SLOW) from error
        except asyncio.CancelledError:
            if deadline.passed():
                self._failed(admitted, "TimeoutError")
            else:
                self._breaker.abandoned(admitted)
            raise
        except Exception as error:
            failure = self._classify(error)
            if failure is None:
                self._succeeded(admitted)
                raise
            self._failed(admitted, type(error).__name__)
            if failure is Failure.PASSING:
                raise _MayPassError(UNAVAILABLE) from error
            raise BackendUnavailableError(UNAVAILABLE) from error
        self._succeeded(admitted)
        return result

    def _succeeded(self, admitted: Admission) -> None:
        if self._breaker.succeeded(admitted):
            _LOG.info("breaker_closed", backend=self.backend)

    def _failed(self, admitted: Admission, error: str) -> None:
        opened = self._breaker.failed(admitted)
        _LOG.warning("backend_failed", backend=self.backend, error=error)
        if opened:
            _LOG.error("breaker_opened", backend=self.backend)

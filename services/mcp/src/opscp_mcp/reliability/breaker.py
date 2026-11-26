"""A circuit breaker: after repeated failures, stop calling a backend for a while.

Closed, every call goes through and failures are counted over a moving window. Enough of
them open the breaker, and calls are refused at once without touching the backend. After a
pause one call is let through as a probe: if it succeeds the breaker closes, if it fails the
pause starts again. Only the probe decides: a call let through before the breaker opened may
finish later, and what happens to it says nothing about the backend now. The state is kept
per process, so each replica learns for itself.
"""

import time
from collections import deque
from collections.abc import Callable
from enum import StrEnum


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class Admission(StrEnum):
    REFUSED = "refused"
    CALL = "call"
    PROBE = "probe"


class CircuitBreaker:
    def __init__(
        self,
        *,
        failures: int,
        window_s: float,
        half_open_after_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._failures = failures
        self._window_s = window_s
        self._half_open_after_s = half_open_after_s
        self._clock = clock
        self._recent: deque[float] = deque()
        self._opened_at: float | None = None
        self._probing = False

    @property
    def state(self) -> BreakerState:
        if self._opened_at is None:
            return BreakerState.CLOSED
        if self._clock() - self._opened_at < self._half_open_after_s:
            return BreakerState.OPEN
        return BreakerState.HALF_OPEN

    def admit(self) -> Admission:
        """Whether a call may be made now, and as what. The outcome is reported with it."""
        state = self.state
        if state is BreakerState.CLOSED:
            return Admission.CALL
        if state is BreakerState.HALF_OPEN and not self._probing:
            self._probing = True
            return Admission.PROBE
        return Admission.REFUSED

    def succeeded(self, admitted: Admission) -> bool:
        """Returns whether this closed the breaker."""
        if admitted is not Admission.PROBE:
            return False
        self._opened_at = None
        self._probing = False
        self._recent.clear()
        return True

    def failed(self, admitted: Admission) -> bool:
        """Returns whether this opened the breaker."""
        now = self._clock()
        if admitted is Admission.PROBE:
            self._opened_at = now
            self._probing = False
            return False
        if self._opened_at is not None:
            return False
        self._recent.append(now)
        while self._recent[0] <= now - self._window_s:
            self._recent.popleft()
        if len(self._recent) < self._failures:
            return False
        self._opened_at = now
        self._recent.clear()
        return True

    def abandoned(self, admitted: Admission) -> None:
        """A call ended without showing whether the backend works, so another may probe."""
        if admitted is Admission.PROBE:
            self._probing = False

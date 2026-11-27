"""An audit trail and a rate limiter that live in memory and behave like the real ones."""

import json
import math
from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from opscp_mcp.ports.controls import Allowance, AuditEntry

_SECONDS_PER_MINUTE = 60


class FakeAuditTrail:
    def __init__(self) -> None:
        self.entries: list[AuditEntry] = []
        self.down = False

    async def record(self, entry: AuditEntry) -> int:
        if "\\u0000" in json.dumps(entry.args_redacted):
            raise ValueError("the database cannot store a NUL character")
        if self.down:
            raise ConnectionError("the audit database is down")
        self.entries.append(entry)
        return len(self.entries)


class FakeRateLimiter:
    """The same token bucket as the database's, with a clock the test controls."""

    def __init__(self, *, per_minute: int, burst: int, clock: Callable[[], datetime]) -> None:
        self._per_minute = per_minute
        self._burst = burst
        self._clock = clock
        self._buckets: dict[UUID, tuple[float, datetime]] = {}

    async def take(self, user_oid: UUID) -> Allowance:
        now = self._clock()
        tokens, refilled_at = self._buckets.get(user_oid, (float(self._burst), now))
        elapsed_s = max((now - refilled_at).total_seconds(), 0)
        tokens = min(self._burst, tokens + elapsed_s * self._per_minute / _SECONDS_PER_MINUTE)
        allowed = tokens >= 1
        self._buckets[user_oid] = (tokens - 1 if allowed else tokens, max(now, refilled_at))
        if allowed:
            return Allowance(allowed=True, retry_after_s=0)
        wait_s = (1 - tokens) * _SECONDS_PER_MINUTE / self._per_minute
        return Allowance(allowed=False, retry_after_s=max(1, math.ceil(wait_s)))

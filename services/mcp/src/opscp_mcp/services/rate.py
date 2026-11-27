"""Counting one request against the person who made it."""

import asyncio
from uuid import UUID
from weakref import WeakValueDictionary

import structlog

from opscp_common.errors import RateLimitedError
from opscp_mcp.authz.identity import Identity
from opscp_mcp.ports.controls import Allowance, RateLimiter
from opscp_mcp.reliability.deadline import within

_LOG = structlog.get_logger(__name__)


class OneAtATime:
    """A rate limiter that counts each person's requests one after another.

    A flood from one person then waits on itself in this process and holds a single place
    among the calls to the database, instead of every place.
    """

    def __init__(self, rates: RateLimiter) -> None:
        self._rates = rates
        self._counting: WeakValueDictionary[UUID, asyncio.Lock] = WeakValueDictionary()

    async def take(self, user_oid: UUID) -> Allowance:
        turn = self._counting.get(user_oid)
        if turn is None:
            turn = self._counting[user_oid] = asyncio.Lock()
        async with turn:
            return await self._rates.take(user_oid)


async def count_request(rates: RateLimiter, caller: Identity, timeout_s: int) -> None:
    """Raises RateLimitedError when the caller has no requests left for now."""
    allowance = await within(timeout_s, rates.take(caller.oid))
    if not allowance.allowed:
        _LOG.info("rate_limited", actor_oid=str(caller.oid), retry_after_s=allowance.retry_after_s)
        raise RateLimitedError(
            "Too many requests. Wait before trying again.",
            retry_after_seconds=allowance.retry_after_s,
        )

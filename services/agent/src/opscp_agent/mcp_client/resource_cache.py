"""Reference texts, kept until their version changes.

The texts are the same for everyone who may read them, so one copy is kept for the
process. A person is given a text only if the server lists it for them, and it is read
again only when the listed version differs from the one held. Server health changes by
itself, so it is kept for a few seconds at most.
"""

import time
from collections.abc import Callable

from opscp_agent.mcp_client.types import McpCallError, McpSession, Reference

ORDER_LIFECYCLE_URI = "meridian://reference/order-lifecycle"
REFUND_SUMMARY_URI = "meridian://policy/refund/summary"
RUNBOOK_INDEX_URI = "meridian://runbooks/index"
HEALTH_URI = "meridian://server/health"
REFERENCE_URIS = (ORDER_LIFECYCLE_URI, REFUND_SUMMARY_URI, RUNBOOK_INDEX_URI)


class ResourceCache:
    def __init__(self, health_cache_s: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._health_cache_s = health_cache_s
        self._clock = clock
        self._held: dict[str, Reference] = {}
        self._health: tuple[float, Reference] | None = None

    async def references(self, mcp: McpSession) -> list[Reference]:
        """The reference texts this person may read, and the server's health."""
        listed = await mcp.resource_versions()
        found: list[Reference] = []
        for uri in REFERENCE_URIS:
            if uri not in listed:
                continue
            held = self._held.get(uri)
            if held is None or held.version is None or held.version != listed[uri]:
                held = await self._read(mcp, uri)
                if held is None:
                    continue
                self._held[uri] = held
            found.append(held)
        health = await self._current_health(mcp) if HEALTH_URI in listed else None
        return found if health is None else [*found, health]

    async def _read(self, mcp: McpSession, uri: str) -> Reference | None:
        try:
            return await mcp.resource(uri)
        except McpCallError:
            return None

    async def _current_health(self, mcp: McpSession) -> Reference | None:
        now = self._clock()
        if self._health is not None and now - self._health[0] < self._health_cache_s:
            return self._health[1]
        health = await self._read(mcp, HEALTH_URI)
        if health is not None:
            self._health = (now, health)
        return health

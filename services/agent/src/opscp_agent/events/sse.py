"""Streams a run's events to a browser as server-sent events.

Events are read from the store, where each was written before anyone was told of it. A
browser that reconnects names the last number it saw and is sent everything after it, so a
dropped connection loses nothing, whichever replica answers.
"""

import asyncio
import json
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any
from uuid import UUID

from opscp_agent.events.bus import ANY_RUN, Bus
from opscp_agent.events.types import FINAL, StoredEvent
from opscp_agent.runs.store import RunStore
from opscp_common.limits import StreamLimits

_MS_PER_S = 1000
KEEPALIVE = ": keepalive\n\n"
MEDIA_TYPE = "text/event-stream"
HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}

type Shown = Callable[[StoredEvent], dict[str, Any]]
type Gone = Callable[[], Awaitable[bool]]


def frame(event: str, data: dict[str, Any], seq: int | None = None) -> str:
    numbered = "" if seq is None else f"id: {seq}\n"
    return f"{numbered}event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def as_stored(event: StoredEvent) -> dict[str, Any]:
    return event.payload


class EventStream:
    def __init__(self, store: RunStore, bus: Bus, limits: StreamLimits) -> None:
        self._store = store
        self._bus = bus
        self._limits = limits

    async def of_run(
        self, run_id: UUID, after: int, gone: Gone, shown: Shown = as_stored
    ) -> AsyncGenerator[str]:
        """The run's events after number `after`, until the run ends or the reader leaves."""
        quiet_since = time.monotonic()
        while not await gone():
            batch = await self._store.events_after(run_id, after, self._limits.page)
            for event in batch:
                after = event.seq
                yield frame(event.type, shown(event), event.seq)
                if event.type in FINAL:
                    return
            if batch:
                quiet_since = time.monotonic()
                continue
            if await self._over(run_id, after):
                return
            if time.monotonic() - quiet_since >= self._limits.keepalive_s:
                quiet_since = time.monotonic()
                yield KEEPALIVE
            await self._bus.wait(str(run_id), self._limits.poll_ms / _MS_PER_S)

    async def _over(self, run_id: UUID, after: int) -> bool:
        """Whether the run has ended and the reader has every event of it.

        A browser reconnects by itself after the last event. Without this it would be
        answered with a stream that never says anything and never ends.
        """
        run = await self._store.get(run_id)
        if run is not None and run.finished_at is None:
            return False
        return not await self._store.events_after(run_id, after, 1)

    async def of_latest_run(self, gone: Gone, shown: Shown) -> AsyncGenerator[str]:
        """Follows whichever run started most recently, moving on when a newer one starts."""
        following: UUID | None = None
        after = 0
        quiet_since = time.monotonic()
        while not await gone():
            latest = await self._store.latest_id()
            if latest is not None and latest != following:
                following, after = latest, 0
                yield frame("run", {"run_id": str(latest)})
            batch = (
                []
                if following is None
                else await self._store.events_after(following, after, self._limits.page)
            )
            for event in batch:
                after = event.seq
                yield frame(event.type, shown(event), event.seq)
            if batch:
                quiet_since = time.monotonic()
                continue
            if time.monotonic() - quiet_since >= self._limits.keepalive_s:
                quiet_since = time.monotonic()
                yield KEEPALIVE
            await self._bus.wait(ANY_RUN, self._limits.poll_ms / _MS_PER_S)


async def never_gone() -> bool:
    await asyncio.sleep(0)
    return False

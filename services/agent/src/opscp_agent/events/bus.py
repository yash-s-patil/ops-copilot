"""Wakes the streams of this process when a run has a new event.

The events themselves are read from the store, which is what every replica shares. This
only saves a stream in the same process from waiting for its next look.
"""

import asyncio
from collections import defaultdict

ANY_RUN = "*"


class Bus:
    def __init__(self) -> None:
        self._waiting: defaultdict[str, set[asyncio.Future[None]]] = defaultdict(set)

    def is_idle(self) -> bool:
        """Whether nobody is waiting and nothing is kept for a topic nobody waits on."""
        return not self._waiting

    def publish(self, run_id: str) -> None:
        for topic in (run_id, ANY_RUN):
            for waiter in self._waiting.pop(topic, ()):
                if not waiter.done():
                    waiter.set_result(None)

    async def wait(self, topic: str, timeout_s: float) -> None:
        """Returns when something is published on the topic, or when the time is up."""
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiting[topic].add(waiter)
        try:
            async with asyncio.timeout(timeout_s):
                await waiter
        except TimeoutError:
            return
        finally:
            waiting = self._waiting.get(topic)
            if waiting is not None:
                waiting.discard(waiter)
                if not waiting:
                    del self._waiting[topic]

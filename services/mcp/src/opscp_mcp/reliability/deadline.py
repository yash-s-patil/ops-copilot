"""The time a piece of work is given, known to whatever runs inside it.

A caller's time limit and a backend's can run out at the same moment, and then the caller's
fires first and cancels the backend call. The guard looks here to tell that from a client
that went away: a call cut off because its time ran out counts against the backend.
"""

import asyncio
import time
from collections.abc import Awaitable
from contextvars import ContextVar

from opscp_common.errors import BackendTimeoutError

TOO_SLOW = "The request took too long. Try again."

_DEADLINE: ContextVar[float | None] = ContextVar("deadline", default=None)


def passed() -> bool:
    """Whether the work now running has used up the time it was given."""
    deadline = _DEADLINE.get()
    return deadline is not None and time.monotonic() >= deadline


async def within[T](timeout_s: float, work: Awaitable[T]) -> T:
    """The work's result, or TIMEOUT for the caller when it takes longer than allowed."""
    outer = _DEADLINE.get()
    own = time.monotonic() + timeout_s
    marker = _DEADLINE.set(own if outer is None else min(own, outer))
    try:
        async with asyncio.timeout(timeout_s):
            return await work
    except TimeoutError as error:
        raise BackendTimeoutError(TOO_SLOW) from error
    finally:
        _DEADLINE.reset(marker)

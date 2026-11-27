"""Lets a write be audited inside its own transaction.

The audit step around every tool call offers the entry it would write. A tool that carries
out a write takes the offer, has the entry stored with the write, and reports the entry's
number. The audit step then writes nothing more for that call.
"""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from opscp_common.errors import OpsError
from opscp_mcp.ports.controls import AuditEntry
from opscp_mcp.ports.execution import EntryFor


@dataclass(slots=True)
class Recording:
    offer: EntryFor
    audit_id: int | None = None
    stored: AuditEntry | None = None
    """The entry as it was last made for storing, so the log line can repeat it exactly."""

    def entry_for(self, refusal: OpsError | None) -> AuditEntry:
        self.stored = self.offer(refusal)
        return self.stored


_CURRENT: ContextVar[Recording | None] = ContextVar("recording", default=None)


@contextmanager
def offered(entry_for: EntryFor) -> Generator[Recording]:
    recording = Recording(entry_for)
    marker = _CURRENT.set(recording)
    try:
        yield recording
    finally:
        _CURRENT.reset(marker)


def current() -> Recording | None:
    return _CURRENT.get()

"""The outbox and the refunds behind it, as far as the worker may see and change them."""

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from opscp_payments.records import Record

type HandoffStatus = Literal["pending", "submitted"]
type AuditAction = Literal["submit", "settle"]
type AuditOutcome = Literal["ok", "error"]


class Handoff(Record):
    """One outbox row held for this worker, with the refund it is about."""

    outbox_id: UUID
    status: HandoffStatus
    attempts: int
    trace_id: str | None
    refund_id: int
    order_id: int
    ticket_id: int | None
    amount_minor: int
    currency: str
    provider_ref: str | None


class AuditEntry(Record):
    trace_id: str | None
    target: str
    action: AuditAction
    args: dict[str, int | str]
    outcome: AuditOutcome
    error_code: str | None
    duration_ms: int


class Step(Protocol):
    """What one step may record. Everything recorded in a step commits together, or not at all."""

    async def take_forced_decline(self) -> bool:
        """Claims the failure drill's one use. True for exactly one caller while it is on."""
        ...

    async def return_forced_decline(self) -> None:
        """Puts the drill back when the submission it was claimed for cannot have arrived."""
        ...

    async def submitted(self, provider_ref: str, entry: AuditEntry) -> int:
        """The provider accepted the refund: it is now sent to payments.

        This and the two below return the number of the audit row stored with the change.
        """
        ...

    async def paid(self, note: str, entry: AuditEntry) -> int: ...

    async def failed(self, reason: str, trouble: str, note: str, entry: AuditEntry) -> int:
        """The refund will not be paid by this row. A person may send it round again."""
        ...

    async def later(self, at: datetime, *, trouble: str | None) -> None:
        """Leaves the row for another attempt. Naming a trouble counts the attempt."""
        ...


type Work = Callable[[Handoff, Step], Awaitable[None]]


class Outbox(Protocol):
    async def due(self, now: datetime, limit: int) -> list[UUID]:
        """The rows waiting to be worked on, oldest first."""
        ...

    async def step(self, outbox_id: UUID, now: datetime, work: Work) -> bool:
        """Holds the row, runs `work` on it, and commits what it recorded.

        False when the row is held by another worker, is no longer due, or is finished.
        If `work` raises, nothing it recorded is kept and the row is as it was.
        """
        ...

    async def set_aside(self, outbox_id: UUID, until: datetime) -> None:
        """Keeps a row whose step broke from being taken again before `until`.

        Its attempts are not counted: a defect in the worker is not the provider's failure.
        """
        ...

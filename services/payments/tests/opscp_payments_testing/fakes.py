"""The outbox and the provider in memory, with the same all-or-nothing rule as the real ones."""

import copy
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from opscp_payments.ports.outbox import AuditEntry, Handoff, Work
from opscp_payments.ports.payments import (
    Accepted,
    ProviderUnavailableError,
    Settlement,
    Submission,
    Trouble,
)
from opscp_payments.sim.store import DECLINE_REASON, Order, Store


@dataclass(slots=True)
class Row:
    """One outbox row together with the refund it is about."""

    outbox_id: UUID
    refund_id: int
    next_attempt_at: datetime
    amount_minor: int = 62000
    order_id: int = 4471
    ticket_id: int | None = 2207
    currency: str = "INR"
    trace_id: str | None = "trace-1"
    status: str = "pending"
    attempts: int = 0
    last_error: str | None = None
    refund_status: str = "approved"
    provider_ref: str | None = None
    failure_reason: str | None = None
    paid_at: datetime | None = None


@dataclass(slots=True)
class _State:
    rows: dict[UUID, Row] = field(default_factory=dict[UUID, Row])
    notes: list[tuple[int, str]] = field(default_factory=list[tuple[int, str]])
    audit: list[AuditEntry] = field(default_factory=list[AuditEntry])
    forced_decline: bool = False


class CrashedBeforeCommitError(Exception):
    """Stands for the worker stopping after the provider answered and before the commit."""


class _Step:
    def __init__(self, state: _State, row: Row, now: datetime) -> None:
        self._state = state
        self._row = row
        self._now = now

    async def take_forced_decline(self) -> bool:
        taken, self._state.forced_decline = self._state.forced_decline, False
        return taken

    async def return_forced_decline(self) -> None:
        self._state.forced_decline = True

    async def submitted(self, provider_ref: str, entry: AuditEntry) -> int:
        row = self._row
        assert row.refund_status == "approved"
        row.refund_status, row.provider_ref, row.failure_reason = (
            "sent_to_payments",
            provider_ref,
            None,
        )
        row.status, row.attempts, row.next_attempt_at, row.last_error = (
            "submitted",
            0,
            self._now,
            None,
        )
        return self._append(entry)

    async def paid(self, note: str, entry: AuditEntry) -> int:
        row = self._row
        assert row.refund_status == "sent_to_payments"
        row.refund_status, row.paid_at, row.status, row.last_error = "paid", self._now, "done", None
        self._note(note)
        return self._append(entry)

    async def failed(self, reason: str, trouble: str, note: str, entry: AuditEntry) -> int:
        row = self._row
        assert row.refund_status in ("approved", "sent_to_payments")
        row.refund_status, row.failure_reason = "failed", reason
        row.status, row.attempts, row.last_error = "failed", row.attempts + 1, trouble
        self._note(note)
        return self._append(entry)

    async def later(self, at: datetime, *, trouble: str | None) -> None:
        row = self._row
        row.next_attempt_at = at
        if trouble is not None:
            row.attempts, row.last_error = row.attempts + 1, trouble

    def _note(self, body: str) -> None:
        if self._row.ticket_id is not None:
            self._state.notes.append((self._row.ticket_id, body))

    def _append(self, entry: AuditEntry) -> int:
        self._state.audit.append(entry)
        return len(self._state.audit)


class FakeOutbox:
    def __init__(self, *rows: Row) -> None:
        self._state = _State(rows={row.outbox_id: row for row in rows})
        self.crash_before_commit = False

    @property
    def notes(self) -> list[tuple[int, str]]:
        return self._state.notes

    @property
    def audit(self) -> list[AuditEntry]:
        return self._state.audit

    @property
    def forced_decline(self) -> bool:
        return self._state.forced_decline

    @forced_decline.setter
    def forced_decline(self, on: bool) -> None:
        self._state.forced_decline = on

    def row(self, outbox_id: UUID) -> Row:
        return self._state.rows[outbox_id]

    async def due(self, now: datetime, limit: int) -> list[UUID]:
        waiting = [
            row
            for row in self._state.rows.values()
            if row.status in ("pending", "submitted") and row.next_attempt_at <= now
        ]
        waiting.sort(key=lambda row: (row.next_attempt_at, str(row.outbox_id)))
        return [row.outbox_id for row in waiting[:limit]]

    async def step(self, outbox_id: UUID, now: datetime, work: Work) -> bool:
        row = self._state.rows.get(outbox_id)
        if row is None or row.status not in ("pending", "submitted") or row.next_attempt_at > now:
            return False
        before = copy.deepcopy(self._state)
        handoff = Handoff(
            outbox_id=row.outbox_id,
            status="pending" if row.status == "pending" else "submitted",
            attempts=row.attempts,
            trace_id=row.trace_id,
            refund_id=row.refund_id,
            order_id=row.order_id,
            ticket_id=row.ticket_id,
            amount_minor=row.amount_minor,
            currency=row.currency,
            provider_ref=row.provider_ref,
        )
        try:
            await work(handoff, _Step(self._state, row, now))
            if self.crash_before_commit:
                raise CrashedBeforeCommitError
        except BaseException:
            self._state = before
            raise
        return True

    async def set_aside(self, outbox_id: UUID, until: datetime) -> None:
        row = self._state.rows[outbox_id]
        if row.status in ("pending", "submitted"):
            row.next_attempt_at, row.last_error = until, "worker_error"


class FakeProvider:
    """The simulator's own store behind the provider port, with troubles a test lines up."""

    def __init__(self, store: Store) -> None:
        self._store = store
        self.troubles: list[Trouble] = []
        self.submissions: list[Submission] = []
        self.asked_about: list[str] = []
        self.honours_forced_outcome = True

    async def submit(self, submission: Submission) -> Accepted:
        self.submissions.append(submission)
        self._maybe_fail()
        decline = self.honours_forced_outcome and submission.forced_outcome == "decline"
        order = Order(submission.order_id, submission.amount_minor, submission.currency)
        refund = self._store.accept(submission.idempotency_key, order, decline=decline)
        return Accepted(provider_ref=refund.provider_ref)

    async def status(self, provider_ref: str) -> Settlement:
        self.asked_about.append(provider_ref)
        self._maybe_fail()
        refund = self._store.find(provider_ref)
        if refund is None:
            raise ProviderUnavailableError("refused")
        status = self._store.status(refund)
        return Settlement(
            status=status, reason_code=DECLINE_REASON if status == "declined" else None
        )

    def _maybe_fail(self) -> None:
        if self.troubles:
            raise ProviderUnavailableError(self.troubles.pop(0))

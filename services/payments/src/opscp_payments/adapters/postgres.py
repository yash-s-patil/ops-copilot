"""The outbox in PostgreSQL, as the role `payments_worker`.

That role can read refunds and the outbox, change a refund's four payment columns, add a
note written by the system, and append to the audit log. It cannot read a customer, an
order or a ticket, create a refund, or change an amount.
"""

import json
from datetime import datetime
from uuid import UUID

from sqlalchemy import CursorResult, TextClause, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from opscp_payments.ports.outbox import AuditEntry, Handoff, Work
from opscp_payments.settings import SUBMIT_ROLE

NOTE_AUTHOR = "Payments"
FORCED_DECLINE_FLAG = "payment_fail"
WORKER_ERROR = "worker_error"

_DUE = text(
    """
    SELECT id FROM control.outbox
    WHERE status IN ('pending', 'submitted') AND next_attempt_at <= :now
    ORDER BY next_attempt_at, id
    LIMIT :limit
    """
)
# Held until the transaction ends. A row another worker holds is passed over, not waited for.
_HOLD = text(
    """
    SELECT o.id AS outbox_id, o.status, o.attempts, o.trace_id,
           r.id AS refund_id, r.order_id, r.ticket_id, r.amount_minor,
           r.currency::text AS currency, r.provider_ref
    FROM control.outbox AS o
    JOIN core.refunds AS r ON r.id = o.refund_id
    WHERE o.id = :outbox_id AND o.status IN ('pending', 'submitted')
      AND o.next_attempt_at <= :now
    FOR UPDATE OF o SKIP LOCKED
    """
)
_REFUND_SENT = text(
    """
    UPDATE core.refunds
    SET status = 'sent_to_payments', provider_ref = :provider_ref, failure_reason = NULL,
        updated_at = :now
    WHERE id = :refund_id AND status = 'approved'
    """
)
_REFUND_PAID = text(
    """
    UPDATE core.refunds SET status = 'paid', paid_at = :now, updated_at = :now
    WHERE id = :refund_id AND status = 'sent_to_payments'
    """
)
_REFUND_FAILED = text(
    """
    UPDATE core.refunds SET status = 'failed', failure_reason = :reason, updated_at = :now
    WHERE id = :refund_id AND status IN ('approved', 'sent_to_payments')
    """
)
_OUTBOX_SUBMITTED = text(
    """
    UPDATE control.outbox
    SET status = 'submitted', attempts = 0, next_attempt_at = :now, last_error = NULL,
        updated_at = :now
    WHERE id = :outbox_id
    """
)
_OUTBOX_DONE = text(
    "UPDATE control.outbox SET status = 'done', last_error = NULL, updated_at = :now "
    "WHERE id = :outbox_id"
)
_OUTBOX_FAILED = text(
    "UPDATE control.outbox SET status = 'failed', attempts = attempts + 1, "
    "last_error = :trouble, updated_at = :now WHERE id = :outbox_id"
)
_OUTBOX_LATER = text(
    """
    UPDATE control.outbox
    SET next_attempt_at = :at,
        attempts = attempts + CASE WHEN CAST(:trouble AS text) IS NULL THEN 0 ELSE 1 END,
        last_error = COALESCE(:trouble, last_error),
        updated_at = :now
    WHERE id = :outbox_id
    """
)
_SET_ASIDE = text(
    "UPDATE control.outbox SET next_attempt_at = :until, last_error = :trouble, "
    "updated_at = now() WHERE id = :outbox_id AND status IN ('pending', 'submitted')"
)
_ADD_NOTE = text(
    "INSERT INTO core.ticket_notes (ticket_id, author_kind, author_name, body, created_at) "
    "VALUES (:ticket_id, 'system', :author, :body, :now)"
)
_TAKE_FORCED_DECLINE = text(
    "UPDATE control.chaos_flags SET enabled = false WHERE name = :name AND enabled RETURNING name"
)
_RETURN_FORCED_DECLINE = text("UPDATE control.chaos_flags SET enabled = true WHERE name = :name")
_APPEND = text(
    """
    SELECT audit.append(
        :trace_id, NULL, NULL, :actor_roles, 'service', :target, :action,
        CAST(:args AS jsonb), :outcome, :error_code, NULL, NULL, :duration_ms
    )
    """
)


class OutOfStepError(Exception):
    """A row was not in the state its step requires. Nothing the step recorded is kept."""


def _exactly_one(changed: CursorResult[tuple[()]], what: str) -> None:
    if changed.rowcount != 1:
        raise OutOfStepError(what)


class _Step:
    def __init__(self, connection: AsyncConnection, handoff: Handoff, now: datetime) -> None:
        self._connection = connection
        self._handoff = handoff
        self._now = now

    async def take_forced_decline(self) -> bool:
        taken = await self._connection.execute(_TAKE_FORCED_DECLINE, {"name": FORCED_DECLINE_FLAG})
        return taken.first() is not None

    async def return_forced_decline(self) -> None:
        await self._connection.execute(_RETURN_FORCED_DECLINE, {"name": FORCED_DECLINE_FLAG})

    async def submitted(self, provider_ref: str, entry: AuditEntry) -> int:
        await self._refund(_REFUND_SENT, provider_ref=provider_ref)
        await self._outbox(_OUTBOX_SUBMITTED)
        return await self._append(entry)

    async def paid(self, note: str, entry: AuditEntry) -> int:
        await self._refund(_REFUND_PAID)
        await self._outbox(_OUTBOX_DONE)
        await self._note(note)
        return await self._append(entry)

    async def failed(self, reason: str, trouble: str, note: str, entry: AuditEntry) -> int:
        await self._refund(_REFUND_FAILED, reason=reason)
        await self._outbox(_OUTBOX_FAILED, trouble=trouble)
        await self._note(note)
        return await self._append(entry)

    async def later(self, at: datetime, *, trouble: str | None) -> None:
        await self._outbox(_OUTBOX_LATER, at=at, trouble=trouble)

    async def _refund(self, statement: TextClause, **values: object) -> None:
        row = {"refund_id": self._handoff.refund_id, "now": self._now, **values}
        _exactly_one(await self._connection.execute(statement, row), "refund")

    async def _outbox(self, statement: TextClause, **values: object) -> None:
        row = {"outbox_id": self._handoff.outbox_id, "now": self._now, **values}
        _exactly_one(await self._connection.execute(statement, row), "outbox row")

    async def _note(self, body: str) -> None:
        """A refund asked for without a ticket has nowhere to leave a note."""
        if self._handoff.ticket_id is None:
            return
        note = {
            "ticket_id": self._handoff.ticket_id,
            "author": NOTE_AUTHOR,
            "body": body,
            "now": self._now,
        }
        await self._connection.execute(_ADD_NOTE, note)

    async def _append(self, entry: AuditEntry) -> int:
        row = entry.model_dump(exclude={"args"}) | {
            "actor_roles": [SUBMIT_ROLE],
            "args": json.dumps(entry.args),
        }
        return (await self._connection.execute(_APPEND, row)).scalar_one()


class PostgresOutbox:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def due(self, now: datetime, limit: int) -> list[UUID]:
        async with self._engine.connect() as connection:
            found = await connection.execute(_DUE, {"now": now, "limit": limit})
            return list(found.scalars().all())

    async def step(self, outbox_id: UUID, now: datetime, work: Work) -> bool:
        async with self._engine.begin() as connection:
            held = await connection.execute(_HOLD, {"outbox_id": outbox_id, "now": now})
            row = held.mappings().one_or_none()
            if row is None:
                return False
            handoff = Handoff.model_validate(dict(row))
            await work(handoff, _Step(connection, handoff, now))
            return True

    async def set_aside(self, outbox_id: UUID, until: datetime) -> None:
        async with self._engine.begin() as connection:
            row = {"outbox_id": outbox_id, "until": until, "trouble": WORKER_ERROR}
            await connection.execute(_SET_ASIDE, row)

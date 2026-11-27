"""Retry payment against PostgreSQL: the refund, its instruction to pay and its audit row."""

import asyncio
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from opscp_common.errors import NotFoundError, PermissionDeniedError, PolicyViolationError
from opscp_common.limits import Limits
from opscp_common.vocabulary import PAYMENT_OUTCOME_UNKNOWN
from opscp_mcp.adapters.postgres.actions import PostgresRefundCases
from opscp_mcp.adapters.postgres.audit import broken_links
from opscp_mcp.adapters.postgres.refunds import PostgresRefundRetries
from opscp_mcp.authz.identity import Identity
from opscp_mcp.ports.controls import AuditEntry
from opscp_mcp.services.approvals import Recorded
from opscp_mcp.services.refunds import RefundRetried, RefundService
from opscp_mcp.services.scope import scope_of
from opscp_mcp_testing.identities import ARJUN, KAVITA, PRIYA
from opscp_mcp_testing.world import LOSS_CONFIRMED, NOW, SOUTH_ORDER

from .database import guarded

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("world")]

FAILED, SENT, SOUTH_FAILED = 910001, 910002, 910003
AMOUNT = 100000
ORDER_VALUE = 890000
_ACTION = "00000000-0000-4000-8000-0000009100{:02d}"
_ACTION_COLUMNS = ("action_id", "key", "oid", "region", "order_id")
_ADD_ACTION = text(
    """
    INSERT INTO control.pending_actions
        (id, tool, args, args_hash, requester_oid, requester_name, requester_region,
         approval_kind, status, approver_oid, approver_name, decided_at, executed_at,
         idempotency_key, order_id, expires_at)
    VALUES
        (:action_id, 'issue_refund', '{}'::jsonb, :key, :oid, 'Arjun Mehta', :region,
         'self', 'executed', :oid, 'Arjun Mehta', now(), now(), :key, :order_id,
         now() + interval '1 day')
    """
)
_ADD_REFUND = text(
    """
    INSERT INTO core.refunds
        (id, order_id, region, amount_minor, reason_code, reason_text, status, failure_reason,
         provider_ref, requested_by_oid, requested_by_name, approved_by_oid, approved_by_name,
         pending_action_id)
    VALUES
        (:id, :order_id, :region, :amount, 'lost_in_transit', 'Lost.', :status, :failure,
         'sim_before', :oid, 'Arjun Mehta', :oid, 'Arjun Mehta', :action_id)
    """
)
_ADD_SPENT_OUTBOX = text(
    "INSERT INTO control.outbox (kind, refund_id, payload, status, attempts, last_error) "
    "VALUES ('refund.submit', :id, '{}'::jsonb, 'failed', 5, 'timeout')"
)
_REMOVE = (
    "DELETE FROM control.outbox WHERE refund_id BETWEEN 910000 AND 919999",
    "DELETE FROM core.refunds WHERE id BETWEEN 910000 AND 919999",
    "DELETE FROM control.pending_actions WHERE idempotency_key LIKE 'retry-test-%'",
)
_REFUND = text(
    "SELECT status, amount_minor, failure_reason, provider_ref FROM core.refunds WHERE id = :id"
)
_OUTBOX = text(
    "SELECT status, attempts, payload, last_error FROM control.outbox WHERE refund_id = :id "
    "ORDER BY created_at, status"
)
_LAST_AUDIT = text(
    "SELECT action, outcome, target, args_redacted FROM audit.audit_log ORDER BY id DESC LIMIT 1"
)
_SCOPE_ALL = text("SELECT set_config('app.region', '*', true)")


def _rows(refund_id: int, order_id: int, region: str, status: str) -> dict[str, Any]:
    return {
        "id": refund_id,
        "order_id": order_id,
        "region": region,
        "amount": AMOUNT,
        "status": status,
        "failure": "provider_declined" if status == "failed" else None,
        "oid": ARJUN.oid,
        "action_id": UUID(_ACTION.format(refund_id % 100)),
        "key": f"retry-test-{refund_id}",
    }


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def refunds_whose_payment_ended(migrator: AsyncEngine) -> AsyncGenerator[None]:
    made = (
        _rows(FAILED, LOSS_CONFIRMED, "west", "failed"),
        _rows(SENT, LOSS_CONFIRMED, "west", "sent_to_payments"),
        _rows(SOUTH_FAILED, SOUTH_ORDER, "south", "failed"),
    )
    async with migrator.begin() as connection:
        await connection.execute(_SCOPE_ALL)
        for row in made:
            await connection.execute(_ADD_ACTION, {key: row[key] for key in _ACTION_COLUMNS})
            await connection.execute(_ADD_REFUND, row)
            if row["status"] == "failed":
                await connection.execute(_ADD_SPENT_OUTBOX, {"id": row["id"]})
    yield
    async with migrator.begin() as connection:
        await connection.execute(_SCOPE_ALL)
        for statement in _REMOVE:
            await connection.execute(text(statement))


class _Desk:
    def __init__(self, engine: AsyncEngine, migrator: AsyncEngine, limits: Limits) -> None:
        self.engine = engine
        self._migrator = migrator
        self.refunds = RefundService(
            PostgresRefundRetries(guarded(engine)), limits.approval, lambda: NOW
        )

    def entry(self, identity: Identity) -> AuditEntry:
        return AuditEntry(
            actor_oid=identity.oid,
            actor_roles=tuple(sorted(identity.roles)),
            actor_kind="user",
            target="POST /refunds/{id}/retry",
            action="retry",
            args_redacted={"refund_id": FAILED},
            outcome="ok",
            duration_ms=1,
        )

    async def retry(self, identity: Identity, refund_id: int) -> Recorded[RefundRetried]:
        return await self.refunds.retry(identity, refund_id, lambda: self.entry(identity))

    async def seen(self, statement: Any, **values: Any) -> list[dict[str, Any]]:
        async with self._migrator.connect() as connection:
            await connection.execute(text("SELECT set_config('app.region', '*', false)"))
            found = await connection.execute(statement, values)
            return [dict(row) for row in found.mappings().all()]

    async def audit_rows(self) -> int:
        async with self.engine.connect() as connection:
            return (await broken_links(connection, limit=1))[0]


@pytest.fixture
def desk(mcp_app: AsyncEngine, migrator: AsyncEngine, limits: Limits) -> _Desk:
    return _Desk(mcp_app, migrator, limits)


async def test_a_retry_makes_the_refund_payable_and_queues_it_with_its_audit_row(
    desk: _Desk,
) -> None:
    before = await desk.audit_rows()

    retried = await desk.retry(ARJUN, FAILED)

    (refund,) = await desk.seen(_REFUND, id=FAILED)
    spent, queued = await desk.seen(_OUTBOX, id=FAILED)
    (audit,) = await desk.seen(_LAST_AUDIT)
    assert retried.outcome == RefundRetried(refund_id=FAILED, status="approved")
    assert refund == {
        "status": "approved",
        "amount_minor": AMOUNT,
        "failure_reason": "provider_declined",
        "provider_ref": "sim_before",
    }
    assert (spent["status"], spent["attempts"], spent["last_error"]) == ("failed", 5, "timeout")
    assert queued == {
        "status": "pending",
        "attempts": 0,
        "payload": {
            "amount_minor": AMOUNT,
            "currency": "INR",
            "order_id": LOSS_CONFIRMED,
            "reason_code": "lost_in_transit",
        },
        "last_error": None,
    }
    assert audit == {
        "action": "retry",
        "outcome": "ok",
        "target": "POST /refunds/{id}/retry",
        "args_redacted": {"refund_id": FAILED},
    }
    assert (await desk.audit_rows(), retried.audit_id > 0) == (before + 1, True)


async def test_a_refund_on_its_way_is_left_exactly_as_it_was(desk: _Desk) -> None:
    with pytest.raises(PolicyViolationError) as refused:
        await desk.retry(ARJUN, SENT)

    (refund,) = await desk.seen(_REFUND, id=SENT)
    assert refused.value.body().details == {"rule": "refund_not_failed"}
    assert refund["status"] == "sent_to_payments"
    assert await desk.seen(_OUTBOX, id=SENT) == []


async def test_of_many_retries_at_the_same_moment_one_payment_is_queued(desk: _Desk) -> None:
    outcomes = await asyncio.gather(
        *(desk.retry(ARJUN, FAILED) for _ in range(6)), return_exceptions=True
    )

    taken = [outcome for outcome in outcomes if isinstance(outcome, Recorded)]
    refused = [outcome for outcome in outcomes if isinstance(outcome, PolicyViolationError)]
    assert (len(taken), len(refused)) == (1, 5)
    assert [row["status"] for row in await desk.seen(_OUTBOX, id=FAILED)] == ["failed", "pending"]


async def test_another_regions_refund_cannot_be_found_let_alone_retried(desk: _Desk) -> None:
    with pytest.raises(NotFoundError):
        await desk.retry(ARJUN, SOUTH_FAILED)

    (refund,) = await desk.seen(_REFUND, id=SOUTH_FAILED)
    assert refund["status"] == "failed"
    with pytest.raises(PolicyViolationError) as seen_and_refused:
        await desk.retry(KAVITA, SOUTH_FAILED)
    assert seen_and_refused.value.body().details["rule"] == "request_waiting"


async def test_a_refund_that_does_not_exist_is_not_found(desk: _Desk) -> None:
    with pytest.raises(NotFoundError):
        await desk.retry(ARJUN, 919999)


async def test_an_engineer_who_may_not_ask_for_refunds_is_refused(desk: _Desk) -> None:
    with pytest.raises(PermissionDeniedError):
        await desk.retry(PRIYA, FAILED)

    assert [row["status"] for row in await desk.seen(_OUTBOX, id=FAILED)] == ["failed"]


async def test_a_refund_that_no_longer_fits_beside_newer_ones_is_refused(
    desk: _Desk, migrator: AsyncEngine
) -> None:
    newer = _rows(910004, LOSS_CONFIRMED, "west", "approved") | {"amount": ORDER_VALUE - AMOUNT + 1}
    async with migrator.begin() as connection:
        await connection.execute(_SCOPE_ALL)
        await connection.execute(_ADD_ACTION, {key: newer[key] for key in _ACTION_COLUMNS})
        await connection.execute(_ADD_REFUND, newer)

    with pytest.raises(PolicyViolationError) as refused:
        await desk.retry(ARJUN, FAILED)

    assert refused.value.body().details["rule"] == "exceeds_remaining"
    assert [row["status"] for row in await desk.seen(_OUTBOX, id=FAILED)] == ["failed"]


async def test_nothing_is_changed_when_the_audit_row_cannot_be_written(desk: _Desk) -> None:
    def no_entry() -> AuditEntry:
        raise RuntimeError("the audit entry could not be made")

    with pytest.raises(RuntimeError):
        await desk.refunds.retry(ARJUN, FAILED, no_entry)

    (refund,) = await desk.seen(_REFUND, id=FAILED)
    assert refund["status"] == "failed"
    assert [row["status"] for row in await desk.seen(_OUTBOX, id=FAILED)] == ["failed"]


async def test_a_self_approved_refund_cannot_be_retried_past_the_supervisors_limit(
    desk: _Desk, migrator: AsyncEngine, limits: Limits
) -> None:
    """Invariant 5: failing and retrying must not split a refund under the limit."""
    made_since = limits.approval.self_limit_minor - AMOUNT + 1
    newer = _rows(910005, LOSS_CONFIRMED, "west", "approved") | {"amount": made_since}
    async with migrator.begin() as connection:
        await connection.execute(_SCOPE_ALL)
        await connection.execute(_ADD_ACTION, {key: newer[key] for key in _ACTION_COLUMNS})
        await connection.execute(_ADD_REFUND, newer)

    with pytest.raises(PolicyViolationError) as refused:
        await desk.retry(ARJUN, FAILED)

    assert refused.value.body().details == {"rule": "needs_supervisor"}
    assert [row["status"] for row in await desk.seen(_OUTBOX, id=FAILED)] == ["failed"]


async def test_a_refund_whose_payment_outcome_is_unknown_still_counts_against_its_order(
    desk: _Desk, migrator: AsyncEngine
) -> None:
    """The provider may yet pay it, so no second refund may take its place."""
    cases = PostgresRefundCases(guarded(desk.engine))
    declined = await cases.for_order(scope_of(ARJUN), LOSS_CONFIRMED, now=NOW)
    async with migrator.begin() as connection:
        await connection.execute(_SCOPE_ALL)
        await connection.execute(
            text("UPDATE core.refunds SET failure_reason = :unknown WHERE id = :id"),
            {"unknown": PAYMENT_OUTCOME_UNKNOWN, "id": FAILED},
        )

    unknown = await cases.for_order(scope_of(ARJUN), LOSS_CONFIRMED, now=NOW)
    retried = await desk.retry(ARJUN, FAILED)

    assert declined is not None
    assert unknown is not None
    assert unknown.refunded_minor - declined.refunded_minor == AMOUNT
    assert retried.outcome.status == "approved"


async def test_a_refund_of_unknown_outcome_can_still_be_retried_past_the_limit(
    desk: _Desk, migrator: AsyncEngine, limits: Limits
) -> None:
    """It counted all along; whatever was approved since was approved knowing of it."""
    made_since = limits.approval.self_limit_minor
    newer = _rows(910006, LOSS_CONFIRMED, "west", "approved") | {"amount": made_since}
    async with migrator.begin() as connection:
        await connection.execute(_SCOPE_ALL)
        await connection.execute(_ADD_ACTION, {key: newer[key] for key in _ACTION_COLUMNS})
        await connection.execute(_ADD_REFUND, newer)
        await connection.execute(
            text("UPDATE core.refunds SET failure_reason = :unknown WHERE id = :id"),
            {"unknown": PAYMENT_OUTCOME_UNKNOWN, "id": FAILED},
        )

    assert (await desk.retry(ARJUN, FAILED)).outcome.status == "approved"

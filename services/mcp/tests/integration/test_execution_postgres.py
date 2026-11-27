"""Carrying out an approved write against PostgreSQL: one transaction, taken once."""

import asyncio
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from opscp_common.errors import (
    ApprovalInvalidError,
    BackendUnavailableError,
    PolicyViolationError,
)
from opscp_common.limits import Limits
from opscp_mcp.adapters.local_signer import LocalApprovalSigner
from opscp_mcp.adapters.postgres.actions import PostgresPendingActions, PostgresRefundCases
from opscp_mcp.adapters.postgres.audit import broken_links
from opscp_mcp.adapters.postgres.execution import PostgresExecutions
from opscp_mcp.adapters.postgres.tickets import PostgresTickets
from opscp_mcp.authz.identity import Identity
from opscp_mcp.ports.controls import AuditEntry
from opscp_mcp.ports.execution import Claim, NewRefund
from opscp_mcp.services import recording
from opscp_mcp.services.approvals import ApprovalService
from opscp_mcp.services.execution import ExecutionService
from opscp_mcp.services.proposals import (
    Proposal,
    ProposalService,
    Recommendation,
    RefundRequest,
    TicketUpdate,
)
from opscp_mcp.services.scope import scope_of
from opscp_mcp_testing.identities import ARJUN, KAVITA, PRIYA
from opscp_mcp_testing.signin import approval_key_pem
from opscp_mcp_testing.world import (
    LOSS_CONFIRMED,
    NEVER_SCANNED,
    NOW,
    PAYMENT_HELD,
    PLAIN,
    SOUTH_PLAIN,
    STUCK_ON_ADDRESS,
    WAITING_ON_CUSTOMER,
)

from .database import guarded

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("world")]

GOODWILL = RefundRequest(
    order_id=NEVER_SCANNED,
    amount_minor=50000,
    reason_code="goodwill_delay",
    reason_text="No carrier scan for 80 hours.",
)
FULL = RefundRequest(
    order_id=LOSS_CONFIRMED,
    amount_minor=890000,
    reason_code="lost_in_transit",
    reason_text="The carrier confirmed the loss.",
)
TICKET = WAITING_ON_CUSTOMER
_UNDO = (
    "DELETE FROM control.outbox WHERE refund_id IN (SELECT id FROM core.refunds WHERE"
    " pending_action_id IN (SELECT id FROM control.pending_actions WHERE"
    " idempotency_key LIKE 'ex-%'))",
    "DELETE FROM core.refunds WHERE pending_action_id IN (SELECT id FROM"
    " control.pending_actions WHERE idempotency_key LIKE 'ex-%')",
    "DELETE FROM control.approval_token_uses WHERE pending_action_id IN (SELECT id FROM"
    " control.pending_actions WHERE idempotency_key LIKE 'ex-%')",
    "DELETE FROM control.pending_actions WHERE idempotency_key LIKE 'ex-%'",
    "DELETE FROM core.ticket_notes WHERE author_kind = 'agent_on_behalf' AND body LIKE 'ex-test:%'",
)
_RESTORE_TICKET = text(
    "UPDATE core.tickets SET status = :status, queue = 'L1', version = 1,"
    " escalated_by_oid = NULL, escalated_by_name = NULL, escalated_at = NULL,"
    " recommendation = NULL, order_id = :order_id WHERE id = :ticket_id"
)
_TICKETS_AS_LOADED = (
    (TICKET, "pending_customer", PAYMENT_HELD),
    (PLAIN, "open", STUCK_ON_ADDRESS),
)
_ABOUT_ANOTHER_ORDER = text("UPDATE core.tickets SET order_id = :order_id WHERE id = :ticket_id")
_REFUNDS = text(
    "SELECT r.order_id, r.ticket_id, r.region, r.amount_minor, r.reason_code, r.status,"
    " r.requested_by_name, r.approved_by_name, r.pending_action_id, o.kind, o.status AS queued,"
    " o.payload FROM core.refunds r LEFT JOIN control.outbox o ON o.refund_id = r.id"
    " WHERE r.pending_action_id = :action_id"
)
_ACTION = text(
    "SELECT status, executed_at, result FROM control.pending_actions WHERE id = :action_id"
)
_TOKENS_USED = text(
    "SELECT count(*) FROM control.approval_token_uses WHERE pending_action_id = :action_id"
)
_LAST_AUDIT = text(
    "SELECT action, outcome, error_code, target FROM audit.audit_log ORDER BY id DESC LIMIT 1"
)
_TICKET = text(
    "SELECT status, queue, version, escalated_by_name, recommendation FROM core.tickets"
    " WHERE id = :ticket_id"
)
_NOTES = text(
    "SELECT author_kind, author_oid, author_name, body FROM core.ticket_notes"
    " WHERE ticket_id = :ticket_id AND body LIKE 'ex-test:%'"
)
_FAIL_EARLIER = text(
    "INSERT INTO core.refunds (order_id, region, amount_minor, reason_code, reason_text,"
    " requested_by_oid, requested_by_name, approved_by_oid, approved_by_name, pending_action_id)"
    " SELECT :order_id, 'west', 1, 'goodwill_delay', 'An earlier goodwill refund', requester_oid,"
    " 'x', requester_oid, 'x', id FROM control.pending_actions WHERE id = :action_id"
)


@pytest.fixture(autouse=True)
async def undo_what_the_tests_wrote(migrator: AsyncEngine) -> Any:
    yield
    async with migrator.begin() as connection:
        await connection.execute(text("SELECT set_config('app.region', '*', true)"))
        for statement in _UNDO:
            await connection.execute(text(statement))
        for ticket_id, status, order_id in _TICKETS_AS_LOADED:
            restored = {"ticket_id": ticket_id, "status": status, "order_id": order_id}
            await connection.execute(_RESTORE_TICKET, restored)


class _Desk:
    def __init__(self, engine: AsyncEngine, limits: Limits) -> None:
        database = guarded(engine)
        signer = LocalApprovalSigner(approval_key_pem())
        self.engine = engine
        self.now = NOW
        self.actions = PostgresPendingActions(database)
        self.approvals = ApprovalService(self.actions, signer, limits, lambda: self.now)
        self.proposals = ProposalService(
            PostgresTickets(database),
            PostgresRefundCases(database),
            self.actions,
            ExecutionService(
                self.actions, PostgresExecutions(database), signer, limits, lambda: self.now
            ),
            limits,
            lambda: self.now,
        )

    def entry(self, identity: Identity, action: str = "execute") -> Any:
        def made(failure: Exception | None = None) -> AuditEntry:
            code = None if failure is None else getattr(failure, "code", None)
            return AuditEntry(
                actor_oid=identity.oid,
                actor_roles=tuple(sorted(identity.roles)),
                actor_kind="user",
                target="issue_refund" if action == "execute" else "POST /approvals/{id}/approve",
                action=action,  # type: ignore[arg-type]
                args_redacted={},
                outcome="ok" if failure is None else "error",
                error_code=None if code is None else code.value,
                duration_ms=1,
            )

        return made

    async def approved_refund(
        self, request: RefundRequest, *, by: Identity = ARJUN, key: str | None = None
    ) -> tuple[Proposal, str, str]:
        key = key or f"ex-{uuid4().hex}"
        proposal = await self.proposals.refund(ARJUN, request, idempotency_key=key)
        decided = await self.approvals.approve(
            by, proposal.action_id, proposal.args_hash, self.entry(by, "approve")
        )
        token = decided.outcome.approval
        if token is None:
            token = await self.approvals.token(ARJUN, proposal.action_id)
        return proposal, key, token.token

    async def execute(
        self, request: RefundRequest, key: str, token: str, *, by: Identity = ARJUN
    ) -> Proposal:
        with recording.offered(self.entry(by)):
            return await self.proposals.refund(
                by, request, idempotency_key=key, approval_token=token
            )

    async def one(self, statement: Any, **parameters: Any) -> Any:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT set_config('app.region', '*', false)"))
            return (await connection.execute(statement, parameters)).mappings().all()


@pytest.fixture
def desk(migrator: AsyncEngine, mcp_app: AsyncEngine, limits: Limits) -> _Desk:
    del migrator
    return _Desk(mcp_app, limits)


async def test_an_approved_refund_and_its_outbox_row_are_written_together(desk: _Desk) -> None:
    proposal, key, token = await desk.approved_refund(GOODWILL)
    async with desk.engine.connect() as connection:
        audit_before, _ = await broken_links(connection, limit=1)

    outcome = await desk.execute(GOODWILL, key, token)

    (row,) = await desk.one(_REFUNDS, action_id=proposal.action_id)
    (action,) = await desk.one(_ACTION, action_id=proposal.action_id)
    (audit,) = await desk.one(_LAST_AUDIT)
    async with desk.engine.connect() as connection:
        audit_after, broken = await broken_links(connection, limit=1)
    assert outcome.status == "executed"
    assert outcome.result is not None
    assert dict(row) == {
        "order_id": NEVER_SCANNED,
        "ticket_id": None,
        "region": "west",
        "amount_minor": 50000,
        "reason_code": "goodwill_delay",
        "status": "approved",
        "requested_by_name": "Arjun Mehta",
        "approved_by_name": "Arjun Mehta",
        "pending_action_id": proposal.action_id,
        "kind": "refund.submit",
        "queued": "pending",
        "payload": {
            "amount_minor": 50000,
            "currency": "INR",
            "order_id": NEVER_SCANNED,
            "reason_code": "goodwill_delay",
        },
    }
    assert (action["status"], action["executed_at"], action["result"]) == (
        "executed",
        NOW,
        outcome.result,
    )
    assert dict(audit) == {
        "action": "execute",
        "outcome": "ok",
        "error_code": None,
        "target": "issue_refund",
    }
    assert (audit_after - audit_before, broken) == (1, [])


async def test_a_supervisors_approval_is_named_on_the_refund(desk: _Desk) -> None:
    proposal, key, token = await desk.approved_refund(FULL, by=KAVITA)

    await desk.execute(FULL, key, token)

    (row,) = await desk.one(_REFUNDS, action_id=proposal.action_id)
    assert (row["requested_by_name"], row["approved_by_name"]) == ("Arjun Mehta", "Kavita Rao")


async def test_of_many_executions_at_the_same_moment_exactly_one_writes(desk: _Desk) -> None:
    proposal, key, first = await desk.approved_refund(GOODWILL)
    tokens = [first] + [
        (await desk.approvals.token(ARJUN, proposal.action_id)).token for _ in range(7)
    ]

    outcomes = await asyncio.gather(
        *(desk.execute(GOODWILL, key, token) for token in tokens), return_exceptions=True
    )

    answers = [outcome for outcome in outcomes if isinstance(outcome, Proposal)]
    assert len(answers) == 8
    assert all(answer.status == "executed" for answer in answers)
    assert all(answer.result == answers[0].result for answer in answers)
    assert len(await desk.one(_REFUNDS, action_id=proposal.action_id)) == 1
    (used,) = await desk.one(_TOKENS_USED, action_id=proposal.action_id)
    assert used["count"] == 1


async def test_repeating_an_execution_returns_the_stored_result_and_writes_nothing(
    desk: _Desk,
) -> None:
    proposal, key, token = await desk.approved_refund(GOODWILL)
    first = await desk.execute(GOODWILL, key, token)

    again = await desk.execute(GOODWILL, key, token)

    assert again == first
    assert len(await desk.one(_REFUNDS, action_id=proposal.action_id)) == 1


async def test_a_refund_the_rules_now_refuse_is_not_written_and_the_action_fails(
    desk: _Desk, migrator: AsyncEngine
) -> None:
    proposal, key, token = await desk.approved_refund(GOODWILL)
    earlier, _, _ = await desk.approved_refund(FULL, by=KAVITA)
    async with migrator.begin() as connection:
        await connection.execute(text("SELECT set_config('app.region', '*', true)"))
        await connection.execute(
            _FAIL_EARLIER, {"order_id": NEVER_SCANNED, "action_id": earlier.action_id}
        )

    with pytest.raises(PolicyViolationError) as refused:
        await desk.execute(GOODWILL, key, token)

    (action,) = await desk.one(_ACTION, action_id=proposal.action_id)
    (audit,) = await desk.one(_LAST_AUDIT)
    (used,) = await desk.one(_TOKENS_USED, action_id=proposal.action_id)
    assert refused.value.body().details["rule"] == "goodwill_already_given"
    assert await desk.one(_REFUNDS, action_id=proposal.action_id) == []
    assert (action["status"], action["executed_at"]) == ("failed", None)
    assert action["result"]["code"] == "POLICY_VIOLATION"
    assert (audit["action"], audit["outcome"], audit["error_code"]) == (
        "execute",
        "error",
        "POLICY_VIOLATION",
    )
    assert used["count"] == 1

    repeated = await desk.execute(GOODWILL, key, token)

    assert (repeated.status, repeated.result) == ("failed", None)


async def test_an_outage_during_the_write_leaves_the_approval_usable(desk: _Desk) -> None:
    proposal, key, token = await desk.approved_refund(GOODWILL)
    executions = PostgresExecutions(guarded(desk.engine))

    async def breaks(_action: object, ledger: Any) -> dict[str, Any]:
        await ledger.add_refund(
            NewRefund(
                order_id=NEVER_SCANNED,
                ticket_id=None,
                region="west",
                amount_minor=50000,
                reason_code="goodwill_delay",
                reason_text="Written, then lost with the transaction.",
            )
        )
        raise BackendUnavailableError("The store is not answering.")

    with pytest.raises(BackendUnavailableError):
        await executions.run(
            Claim(
                action_id=proposal.action_id,
                token_id=uuid4(),
                approver_oid=ARJUN.oid,
                args_hash=proposal.args_hash,
                scope=scope_of(ARJUN),
                order_id=NEVER_SCANNED,
                now=NOW,
            ),
            breaks,
            desk.entry(ARJUN),
        )

    (action,) = await desk.one(_ACTION, action_id=proposal.action_id)
    (used,) = await desk.one(_TOKENS_USED, action_id=proposal.action_id)
    assert (action["status"], used["count"]) == ("approved", 0)
    assert await desk.one(_REFUNDS, action_id=proposal.action_id) == []
    assert (await desk.execute(GOODWILL, key, token)).status == "executed"


async def test_an_expired_or_unapproved_action_is_left_exactly_as_it_was(desk: _Desk) -> None:
    proposal, key, token = await desk.approved_refund(GOODWILL)
    desk.now = NOW + timedelta(hours=25)

    with pytest.raises(ApprovalInvalidError):
        await desk.execute(GOODWILL, key, token)

    (action,) = await desk.one(_ACTION, action_id=proposal.action_id)
    (used,) = await desk.one(_TOKENS_USED, action_id=proposal.action_id)
    assert (action["status"], used["count"]) == ("approved", 0)
    assert await desk.one(_REFUNDS, action_id=proposal.action_id) == []


async def test_a_note_and_a_status_change_are_written_to_the_ticket(
    desk: _Desk, limits: Limits
) -> None:
    del limits
    update = TicketUpdate(ticket_id=TICKET, status="open", note="ex-test: customer replied.")
    key = f"ex-{uuid4().hex}"
    proposal = await desk.proposals.ticket_update(ARJUN, update, idempotency_key=key)
    decided = await desk.approvals.approve(
        ARJUN, proposal.action_id, proposal.args_hash, desk.entry(ARJUN, "approve")
    )
    assert decided.outcome.approval is not None

    with recording.offered(desk.entry(ARJUN)):
        outcome = await desk.proposals.ticket_update(
            ARJUN, update, idempotency_key=key, approval_token=decided.outcome.approval.token
        )

    (ticket,) = await desk.one(_TICKET, ticket_id=TICKET)
    (note,) = await desk.one(_NOTES, ticket_id=TICKET)
    assert outcome.result == {"ticket_id": TICKET, "status": "open", "queue": "L1", "version": 2}
    assert (ticket["status"], ticket["queue"], ticket["version"]) == ("open", "L1", 2)
    assert dict(note) == {
        "author_kind": "agent_on_behalf",
        "author_oid": ARJUN.oid,
        "author_name": "Arjun Mehta",
        "body": "ex-test: customer replied.",
    }


async def test_an_escalation_hands_the_ticket_to_l2_with_who_and_why(
    desk: _Desk, migrator: AsyncEngine
) -> None:
    async with migrator.begin() as connection:
        await connection.execute(text("SELECT set_config('app.region', '*', true)"))
        await connection.execute(
            _ABOUT_ANOTHER_ORDER, {"order_id": NEVER_SCANNED, "ticket_id": PLAIN}
        )
    update = TicketUpdate(
        ticket_id=PLAIN,
        escalate_to="L2",
        note="ex-test: needs a refund.",
        recommendation=Recommendation(
            amount_minor=30000, reason_code="goodwill_delay", reason_text="Stuck for days."
        ),
    )
    key = f"ex-{uuid4().hex}"
    proposal = await desk.proposals.ticket_update(PRIYA, update, idempotency_key=key)
    decided = await desk.approvals.approve(
        PRIYA, proposal.action_id, proposal.args_hash, desk.entry(PRIYA, "approve")
    )
    assert decided.outcome.approval is not None

    with recording.offered(desk.entry(PRIYA)):
        outcome = await desk.proposals.ticket_update(
            PRIYA, update, idempotency_key=key, approval_token=decided.outcome.approval.token
        )

    (ticket,) = await desk.one(_TICKET, ticket_id=PLAIN)
    (note,) = await desk.one(_NOTES, ticket_id=PLAIN)
    assert outcome.result == {"ticket_id": PLAIN, "status": "open", "queue": "L2", "version": 2}
    assert dict(ticket) == {
        "status": "open",
        "queue": "L2",
        "version": 2,
        "escalated_by_name": "Priya Nair",
        "recommendation": {
            "amount_minor": 30000,
            "reason_code": "goodwill_delay",
            "reason_text": "Stuck for days.",
        },
    }
    assert note["author_name"] == "Priya Nair"


async def test_a_ticket_changed_since_approval_is_left_alone_and_the_action_fails(
    desk: _Desk, migrator: AsyncEngine
) -> None:
    update = TicketUpdate(ticket_id=TICKET, status="open", note="ex-test: stale.")
    key = f"ex-{uuid4().hex}"
    proposal = await desk.proposals.ticket_update(ARJUN, update, idempotency_key=key)
    decided = await desk.approvals.approve(
        ARJUN, proposal.action_id, proposal.args_hash, desk.entry(ARJUN, "approve")
    )
    assert decided.outcome.approval is not None
    async with migrator.begin() as connection:
        await connection.execute(text("SELECT set_config('app.region', '*', true)"))
        await connection.execute(
            text("UPDATE core.tickets SET version = version + 1 WHERE id = :id"), {"id": TICKET}
        )

    with pytest.raises(PolicyViolationError) as refused, recording.offered(desk.entry(ARJUN)):
        await desk.proposals.ticket_update(
            ARJUN, update, idempotency_key=key, approval_token=decided.outcome.approval.token
        )

    (ticket,) = await desk.one(_TICKET, ticket_id=TICKET)
    (action,) = await desk.one(_ACTION, action_id=proposal.action_id)
    assert refused.value.body().details == {"rule": "ticket_changed"}
    assert (ticket["status"], ticket["version"]) == ("pending_customer", 2)
    assert await desk.one(_NOTES, ticket_id=TICKET) == []
    assert action["status"] == "failed"


async def test_a_write_cannot_reach_another_regions_ticket(desk: _Desk) -> None:
    """Row-level security holds inside the write: the scope set is the caller's."""
    executions = PostgresExecutions(guarded(desk.engine))
    update = TicketUpdate(ticket_id=TICKET, note="ex-test: scope.")
    key = f"ex-{uuid4().hex}"
    proposal = await desk.proposals.ticket_update(PRIYA, update, idempotency_key=key)
    await desk.approvals.approve(
        PRIYA, proposal.action_id, proposal.args_hash, desk.entry(PRIYA, "approve")
    )
    seen: list[object] = []

    async def look(_action: object, ledger: Any) -> dict[str, Any]:
        seen.extend([await ledger.ticket(SOUTH_PLAIN), await ledger.ticket(TICKET)])
        return {}

    await executions.run(
        Claim(
            action_id=proposal.action_id,
            token_id=uuid4(),
            approver_oid=PRIYA.oid,
            args_hash=proposal.args_hash,
            scope=scope_of(PRIYA),
            order_id=None,
            now=NOW,
        ),
        look,
        desk.entry(PRIYA),
    )

    assert seen[0] is None
    assert seen[1] is not None

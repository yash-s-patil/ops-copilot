"""The rules applied at the moment of writing, when what was approved no longer holds."""

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest

from opscp_common.errors import ApprovalInvalidError, ErrorCode, OpsError
from opscp_common.hashing import JsonValue, args_hash
from opscp_common.limits import Limits
from opscp_mcp.adapters.local_signer import LocalApprovalSigner
from opscp_mcp.ports.actions import ActionRecord, RefundCase, WriteTool
from opscp_mcp.ports.controls import AuditEntry
from opscp_mcp.ports.execution import (
    ApprovedAction,
    Claim,
    EntryFor,
    Executed,
    NewNote,
    NewRefund,
    RefusedAtExecutionError,
    TicketChange,
    TicketState,
    Work,
)
from opscp_mcp.ports.meridian import Scope
from opscp_mcp.ports.signing import ApprovalClaims
from opscp_mcp.services import recording
from opscp_mcp.services.execution import ExecutionService
from opscp_mcp.services.proposals import Proposal
from opscp_mcp_testing.fakes.actions import FakePendingActions
from opscp_mcp_testing.identities import ARJUN
from opscp_mcp_testing.signin import approval_key_pem
from opscp_mcp_testing.world import NOW, ago

ORDER = 4471
TICKET = 88
KEY = "key-1"
AUDIT_ID = 5
REFUND_ID = 31
REFUND: dict[str, JsonValue] = {
    "order_id": ORDER,
    "amount_minor": 30000,
    "reason_code": "goodwill_delay",
    "reason_text": "Packed for 100 hours.",
}
RESOLVE: dict[str, JsonValue] = {"ticket_id": TICKET, "status": "resolved"}
ESCALATE: dict[str, JsonValue] = {"ticket_id": TICKET, "escalate_to": "L2"}


def _case() -> RefundCase:
    return RefundCase.model_validate(
        {
            "order_id": ORDER,
            "region": "west",
            "tier": "premium",
            "status": "packed",
            "status_changed_at": ago(100),
            "amount_minor": 620000,
            "refunded_minor": 0,
            "last_new_scan_at": None,
            "carrier_exception_at": None,
            "loss_confirmed": False,
            "damage_reported": False,
            "goodwill_given": False,
            "request_waiting": False,
        }
    )


def _ticket(**changed: Any) -> TicketState:
    facts: dict[str, Any] = {
        "id": TICKET,
        "order_id": ORDER,
        "region": "west",
        "status": "open",
        "queue": "L1",
        "version": 3,
    }
    return TicketState.model_validate(facts | changed)


@dataclass(slots=True)
class _Ledger:
    case: RefundCase | None = field(default_factory=_case)
    held: TicketState | None = field(default_factory=_ticket)
    refund_unfinished: bool = False
    changed_meanwhile: bool = False
    refunds: list[NewRefund] = field(default_factory=list[NewRefund])
    changes: list[TicketChange] = field(default_factory=list[TicketChange])
    notes: list[NewNote] = field(default_factory=list[NewNote])

    async def refund_case(self, order_id: int) -> RefundCase | None:
        del order_id
        return self.case

    async def ticket(self, ticket_id: int) -> TicketState | None:
        del ticket_id
        return self.held

    async def refund_unfinished_on(self, ticket_id: int) -> bool:
        del ticket_id
        return self.refund_unfinished

    async def add_refund(self, refund: NewRefund) -> int:
        self.refunds.append(refund)
        return REFUND_ID

    async def change_ticket(self, change: TicketChange) -> TicketState | None:
        if self.changed_meanwhile or self.held is None:
            return None
        self.changes.append(change)
        return self.held.model_copy(
            update={
                "status": change.status or self.held.status,
                "version": self.held.version + 1,
            }
        )

    async def add_note(self, note: NewNote) -> None:
        self.notes.append(note)


class _Actions(FakePendingActions):
    def __init__(self, desk: "_Desk") -> None:
        super().__init__()
        self._desk = desk

    async def find(self, scope: Scope, tool: WriteTool, idempotency_key: str) -> ActionRecord:
        del scope, tool, idempotency_key
        return self._desk.action


class _Desk:
    """One approved action, a token for it, and a ledger the test sets up."""

    def __init__(self, limits: Limits, tool: WriteTool, args: dict[str, JsonValue]) -> None:
        self.tool: WriteTool = tool
        self.args = args
        self.ledger = _Ledger()
        self.claim_holds = True
        self.taken_meanwhile_as: ActionRecord | None = None
        self.answer: Executed | RefusedAtExecutionError | None = None
        self.entries: list[AuditEntry] = []
        self._signer = LocalApprovalSigner(approval_key_pem())
        self.action = ActionRecord(
            id=uuid4(),
            tool=tool,
            args=args,
            args_hash=args_hash(tool, args),
            requester_oid=ARJUN.oid,
            requester_name="Arjun Mehta",
            requester_region="west",
            approval_kind="self",
            status="approved",
            approver_oid=ARJUN.oid,
            approver_name="Arjun Mehta",
            decided_at=NOW,
            decision_reason=None,
            policy_snapshot={"ticket_version": 3},
            ticket_id=None,
            result=None,
            expires_at=NOW + timedelta(hours=24),
            created_at=NOW,
        )
        self.service = ExecutionService(_Actions(self), self, self._signer, limits, lambda: NOW)

    async def run(self, claim: Claim, work: Work, entry: EntryFor) -> Executed | None:
        del claim
        if isinstance(self.answer, RefusedAtExecutionError):
            raise self.answer
        if self.answer is not None:
            return self.answer
        if self.taken_meanwhile_as is not None:
            self.action = self.taken_meanwhile_as
            return None
        if not self.claim_holds:
            return None
        action = self.action
        assert action.approver_oid is not None
        assert action.approver_name is not None
        approved = ApprovedAction(
            id=action.id,
            tool=action.tool,
            args=action.args,
            approval_kind=action.approval_kind,
            requester_oid=action.requester_oid,
            requester_name=action.requester_name,
            approver_oid=action.approver_oid,
            approver_name=action.approver_name,
            policy_snapshot=action.policy_snapshot,
        )
        try:
            result = await work(approved, self.ledger)
        except OpsError as refusal:
            self.entries.append(entry(refusal))
            raise RefusedAtExecutionError(refusal, AUDIT_ID) from None
        self.entries.append(entry(None))
        return Executed(result=result, audit_id=AUDIT_ID)

    async def token(self) -> str:
        return await self._signer.sign(
            ApprovalClaims(
                token_id=uuid4(),
                requester_oid=ARJUN.oid,
                approver_oid=ARJUN.oid,
                action_id=self.action.id,
                tool=self.tool,
                args_hash=self.action.args_hash,
                issued_at=NOW,
                expires_at=NOW + timedelta(seconds=120),
            )
        )

    async def carry_out(self) -> Proposal:
        return await self.service.carry_out(
            ARJUN, self.tool, self.args, idempotency_key=KEY, approval_token=await self.token()
        )

    async def recorded(self) -> tuple[Proposal | OpsError, int | None]:
        with recording.offered(_entry) as offer:
            try:
                return await self.carry_out(), offer.audit_id
            except OpsError as refusal:
                return refusal, offer.audit_id


def _entry(refusal: OpsError | None) -> AuditEntry:
    return AuditEntry(
        actor_oid=ARJUN.oid,
        actor_roles=("Support.L2",),
        actor_kind="user",
        target="issue_refund",
        action="execute",
        args_redacted={},
        outcome="ok" if refusal is None else "error",
        error_code=None if refusal is None else refusal.code.value,
        duration_ms=0,
    )


def _refused(outcome: Proposal | OpsError) -> tuple[ErrorCode, JsonValue]:
    assert isinstance(outcome, OpsError)
    body = outcome.body()
    return body.code, body.details.get("rule")


async def test_a_refund_is_written_and_its_audit_row_is_named_to_the_caller(
    limits: Limits,
) -> None:
    desk = _Desk(limits, "issue_refund", REFUND | {"ticket_id": TICKET})

    outcome, audit_id = await desk.recorded()

    assert isinstance(outcome, Proposal)
    assert outcome.result == {"refund_id": REFUND_ID, "status": "approved", "amount_minor": 30000}
    assert [(r.ticket_id, r.region, r.reason_code) for r in desk.ledger.refunds] == [
        (TICKET, "west", "goodwill_delay")
    ]
    assert audit_id == AUDIT_ID
    assert [entry.outcome for entry in desk.entries] == ["ok"]


async def test_an_order_no_longer_visible_is_not_refunded(limits: Limits) -> None:
    desk = _Desk(limits, "issue_refund", REFUND)
    desk.ledger.case = None

    outcome, audit_id = await desk.recorded()

    assert _refused(outcome) == (ErrorCode.NOT_FOUND, None)
    assert (desk.ledger.refunds, audit_id) == ([], AUDIT_ID)


@pytest.mark.parametrize("held", [None, _ticket(order_id=ORDER + 1)])
async def test_a_refund_whose_ticket_is_gone_or_about_another_order_is_not_made(
    limits: Limits, held: TicketState | None
) -> None:
    desk = _Desk(limits, "issue_refund", REFUND | {"ticket_id": TICKET})
    desk.ledger.held = held

    outcome, _ = await desk.recorded()

    assert _refused(outcome) == (ErrorCode.NOT_FOUND, None)
    assert desk.ledger.refunds == []


async def test_a_stored_reason_that_is_not_one_of_the_four_is_refused(limits: Limits) -> None:
    desk = _Desk(limits, "issue_refund", REFUND | {"reason_code": "because"})

    outcome, _ = await desk.recorded()

    assert _refused(outcome) == (ErrorCode.POLICY_VIOLATION, "reason")
    assert desk.ledger.refunds == []


async def test_a_refund_without_a_whole_order_number_cannot_be_carried_out(
    limits: Limits,
) -> None:
    desk = _Desk(limits, "issue_refund", REFUND | {"order_id": True})

    with pytest.raises(ApprovalInvalidError):
        await desk.carry_out()

    assert desk.entries == []


async def test_a_claim_lost_to_another_call_is_refused_like_any_bad_token(limits: Limits) -> None:
    desk = _Desk(limits, "issue_refund", REFUND)
    desk.claim_holds = False

    outcome, audit_id = await desk.recorded()

    assert isinstance(outcome, ApprovalInvalidError)
    assert outcome.body().message == "This approval cannot be used."
    assert audit_id is None


async def test_the_same_call_arriving_twice_at_once_is_answered_alike(limits: Limits) -> None:
    desk = _Desk(limits, "issue_refund", REFUND)
    done: dict[str, JsonValue] = {"refund_id": REFUND_ID, "status": "approved"}
    desk.taken_meanwhile_as = desk.action.model_copy(update={"status": "executed", "result": done})

    outcome, audit_id = await desk.recorded()

    assert isinstance(outcome, Proposal)
    assert (outcome.status, outcome.result, audit_id) == ("executed", done, None)
    assert desk.ledger.refunds == []


async def test_a_call_repeated_after_the_rules_refused_it_is_told_it_failed(
    limits: Limits,
) -> None:
    desk = _Desk(limits, "issue_refund", REFUND)
    desk.action = desk.action.model_copy(
        update={"status": "failed", "result": {"code": "POLICY_VIOLATION", "details": {}}}
    )

    outcome, _ = await desk.recorded()

    assert isinstance(outcome, Proposal)
    assert (outcome.status, outcome.result) == ("failed", None)
    assert desk.entries == []


async def test_a_stored_status_that_no_ticket_can_have_is_refused(limits: Limits) -> None:
    desk = _Desk(limits, "update_ticket", {"ticket_id": TICKET, "status": "archived"})

    outcome, _ = await desk.recorded()

    assert _refused(outcome) == (ErrorCode.POLICY_VIOLATION, "status")
    assert desk.ledger.changes == []


async def test_a_ticket_no_longer_visible_is_not_changed(limits: Limits) -> None:
    desk = _Desk(limits, "update_ticket", RESOLVE)
    desk.ledger.held = None

    outcome, _ = await desk.recorded()

    assert _refused(outcome) == (ErrorCode.NOT_FOUND, None)


@pytest.mark.parametrize("held", [_ticket(status="pending_customer"), _ticket(queue="L2")])
async def test_a_ticket_that_is_not_open_in_l1_is_not_escalated(
    limits: Limits, held: TicketState
) -> None:
    desk = _Desk(limits, "update_ticket", ESCALATE)
    desk.ledger.held = held

    outcome, _ = await desk.recorded()

    assert _refused(outcome) == (ErrorCode.POLICY_VIOLATION, "cannot_escalate")
    assert desk.ledger.changes == []


async def test_a_ticket_is_not_resolved_while_a_refund_on_it_is_unfinished(
    limits: Limits,
) -> None:
    desk = _Desk(limits, "update_ticket", RESOLVE)
    desk.ledger.refund_unfinished = True

    outcome, _ = await desk.recorded()

    assert _refused(outcome) == (ErrorCode.POLICY_VIOLATION, "refund_in_progress")
    assert desk.ledger.changes == []


async def test_a_ticket_changed_between_the_check_and_the_write_gets_no_note(
    limits: Limits,
) -> None:
    desk = _Desk(limits, "update_ticket", RESOLVE | {"note": "Customer confirmed delivery."})
    desk.ledger.changed_meanwhile = True

    outcome, _ = await desk.recorded()

    assert _refused(outcome) == (ErrorCode.POLICY_VIOLATION, "ticket_changed")
    assert desk.ledger.notes == []


async def test_a_status_change_with_no_note_writes_none(limits: Limits) -> None:
    desk = _Desk(limits, "update_ticket", RESOLVE)

    outcome, _ = await desk.recorded()

    assert isinstance(outcome, Proposal)
    assert outcome.result == {
        "ticket_id": TICKET,
        "status": "resolved",
        "queue": "L1",
        "version": 4,
    }
    assert desk.ledger.notes == []


async def test_a_write_with_no_audit_entry_offered_is_not_made(limits: Limits) -> None:
    """Outside a tool call nothing offers an entry, and a write is never left unrecorded."""
    desk = _Desk(limits, "issue_refund", REFUND)

    with pytest.raises(RuntimeError, match="no audit entry"):
        await desk.carry_out()

    assert desk.entries == []


async def test_a_refusal_with_nothing_offered_still_reaches_the_caller(limits: Limits) -> None:
    desk = _Desk(limits, "issue_refund", REFUND)

    desk.answer = RefusedAtExecutionError(ApprovalInvalidError("refused"), AUDIT_ID)
    with pytest.raises(ApprovalInvalidError):
        await desk.carry_out()
    desk.answer = Executed(result={"refund_id": REFUND_ID}, audit_id=AUDIT_ID)
    assert (await desk.carry_out()).result == {"refund_id": REFUND_ID}

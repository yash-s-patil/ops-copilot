"""Carrying out an approved write: once, for exactly what was approved, or not at all."""

from dataclasses import replace
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
import structlog

from opscp_common.errors import BackendUnavailableError, ErrorCode
from opscp_common.limits import Limits
from opscp_mcp.adapters.local_signer import LocalApprovalSigner
from opscp_mcp.authz.identity import Identity
from opscp_mcp.container import Services
from opscp_mcp.ports.controls import AuditEntry
from opscp_mcp.ports.execution import Claim
from opscp_mcp.ports.signing import ApprovalClaims
from opscp_mcp.services.scope import scope_of
from opscp_mcp_testing.fakes.controls import FakeAuditTrail
from opscp_mcp_testing.identities import ARJUN, KAVITA, PRIYA
from opscp_mcp_testing.server import (
    call,
    connected,
    data_of,
    error_of,
    fake_services,
    fake_writes,
    server_with,
)
from opscp_mcp_testing.signin import approval_key_pem
from opscp_mcp_testing.world import (
    LOSS_CONFIRMED,
    NEVER_SCANNED,
    NOW,
    PLAIN,
    WORLD,
    RefundRow,
    World,
)

GOODWILL: dict[str, Any] = {
    "order_id": NEVER_SCANNED,
    "amount_minor": 50000,
    "reason_code": "goodwill_delay",
    "reason_text": "No carrier scan for 80 hours.",
}
FULL: dict[str, Any] = {
    "order_id": LOSS_CONFIRMED,
    "amount_minor": 890000,
    "reason_code": "lost_in_transit",
    "reason_text": "The carrier confirmed the loss.",
}
NOTE: dict[str, Any] = {"ticket_id": PLAIN, "note": "Called the customer, no answer."}
REFUSED = "This approval cannot be used."
GARBAGE = "not-a-token"
KEPT_AS = "<credential>"


def _entry() -> AuditEntry:
    return AuditEntry(
        actor_oid=ARJUN.oid,
        actor_roles=("Support.L2",),
        actor_kind="user",
        target="POST /approvals/{id}/approve",
        action="approve",
        args_redacted={},
        outcome="ok",
        duration_ms=0,
    )


class _Bench:
    """One server, a clock the test moves, and everything the fakes have written."""

    def __init__(self, limits: Limits, world: World = WORLD) -> None:
        self.limits = limits
        self.world = world
        self.now = NOW
        self.audit = FakeAuditTrail()
        self.signer = LocalApprovalSigner(approval_key_pem())
        self.writes = fake_writes(world, limits, self.audit, lambda: self.now, self.signer)

    @property
    def written(self) -> Any:
        return self.writes.executions.written

    def _services(self, identity: Identity) -> Services:
        return replace(
            fake_services(identity, self.limits, self.world),
            proposals=self.writes.proposals,
            approvals=self.writes.approvals,
            audit=self.audit,
        )

    async def ask(self, identity: Identity, tool: str, key: str, **arguments: Any) -> Any:
        async with connected(server_with(self._services(identity), self.limits)) as client:
            return await call(client, tool, idempotency_key=key, **arguments)

    async def approved(
        self,
        tool: str,
        arguments: dict[str, Any],
        *,
        by: Identity | None = None,
        asker: Identity = ARJUN,
        key: str = "key-1",
    ) -> str:
        """Asks, has it approved, and returns the token the requester would be given."""
        proposal = data_of(await self.ask(asker, tool, key, **arguments))
        decided = await self.writes.approvals.approve(
            by or asker, UUID(proposal["action_id"]), proposal["args_hash"], _entry
        )
        if decided.outcome.approval is not None:
            return decided.outcome.approval.token
        return (await self.writes.approvals.token(asker, UUID(proposal["action_id"]))).token

    def status(self) -> list[str]:
        return [action.status for action in self.writes.actions.stored]


@pytest.fixture
def bench(limits: Limits) -> _Bench:
    return _Bench(limits)


async def test_an_approved_refund_is_written_with_the_row_that_tells_payments_to_pay_it(
    bench: _Bench,
) -> None:
    token = await bench.approved("issue_refund", GOODWILL)

    outcome = data_of(
        await bench.ask(ARJUN, "issue_refund", "key-1", approval_token=token, **GOODWILL)
    )

    (refund,) = bench.written.refunds
    assert (outcome["status"], outcome["result"]) == (
        "executed",
        {"refund_id": refund.id, "status": "approved", "amount_minor": 50000},
    )
    assert (refund.order_id, refund.amount_minor, refund.region, refund.status) == (
        NEVER_SCANNED,
        50000,
        "west",
        "approved",
    )
    assert (refund.requested_by_name, refund.approved_by_name) == ("Arjun Mehta", "Arjun Mehta")
    assert bench.written.outbox == [
        {
            "kind": "refund.submit",
            "refund_id": refund.id,
            "amount_minor": 50000,
            "order_id": NEVER_SCANNED,
            "reason_code": "goodwill_delay",
        }
    ]
    assert bench.status() == ["executed"]


async def test_a_refund_a_supervisor_approved_names_both_people(bench: _Bench) -> None:
    token = await bench.approved("issue_refund", FULL, by=KAVITA)

    await bench.ask(ARJUN, "issue_refund", "key-1", approval_token=token, **FULL)

    (refund,) = bench.written.refunds
    assert (refund.requested_by_name, refund.approved_by_name) == ("Arjun Mehta", "Kavita Rao")


async def test_repeating_the_call_returns_what_was_done_and_writes_nothing_more(
    bench: _Bench,
) -> None:
    token = await bench.approved("issue_refund", GOODWILL)
    first = data_of(
        await bench.ask(ARJUN, "issue_refund", "key-1", approval_token=token, **GOODWILL)
    )

    again = data_of(
        await bench.ask(ARJUN, "issue_refund", "key-1", approval_token=token, **GOODWILL)
    )
    without = data_of(await bench.ask(ARJUN, "issue_refund", "key-1", **GOODWILL))

    assert first == again == without
    assert (len(bench.written.refunds), len(bench.written.outbox)) == (1, 1)


async def test_a_second_token_for_the_same_action_cannot_write_again(bench: _Bench) -> None:
    first = await bench.approved("issue_refund", GOODWILL)
    action_id = bench.writes.actions.stored[0].id
    second = (await bench.writes.approvals.token(ARJUN, action_id)).token
    await bench.ask(ARJUN, "issue_refund", "key-1", approval_token=first, **GOODWILL)

    again = data_of(
        await bench.ask(ARJUN, "issue_refund", "key-1", approval_token=second, **GOODWILL)
    )

    assert again["status"] == "executed"
    assert len(bench.written.refunds) == 1


def _changed(**changed: Any) -> dict[str, Any]:
    return GOODWILL | changed


WRONG_ARGUMENTS: dict[str, dict[str, Any]] = {
    "another amount": _changed(amount_minor=50001),
    "another order": _changed(order_id=LOSS_CONFIRMED),
    "another reason": _changed(reason_code="damaged"),
    "other words": _changed(reason_text="Something else."),
    "a ticket added": _changed(ticket_id=PLAIN),
}


@pytest.mark.parametrize("arguments", WRONG_ARGUMENTS.values(), ids=WRONG_ARGUMENTS.keys())
async def test_an_approval_does_not_carry_over_to_changed_arguments(
    bench: _Bench, arguments: dict[str, Any]
) -> None:
    token = await bench.approved("issue_refund", GOODWILL)

    refused = error_of(
        await bench.ask(ARJUN, "issue_refund", "key-1", approval_token=token, **arguments)
    )

    assert (refused.code, refused.message) == (ErrorCode.APPROVAL_INVALID, REFUSED)
    assert bench.written.refunds == []
    assert bench.status() == ["approved"]


async def _token(bench: _Bench, **changed: Any) -> str:
    """A token signed by the server's own key, for the stored action unless changed."""
    action = bench.writes.actions.stored[0]
    claims = ApprovalClaims(
        token_id=uuid4(),
        requester_oid=ARJUN.oid,
        approver_oid=ARJUN.oid,
        action_id=action.id,
        tool="issue_refund",
        args_hash=action.new.args_hash,
        issued_at=bench.now,
        expires_at=bench.now + timedelta(seconds=120),
    )
    return await bench.signer.sign(claims.model_copy(update=changed))


WRONG_TOKENS: dict[str, dict[str, Any]] = {
    "for another action": {"action_id": UUID(int=7)},
    "for the other tool": {"tool": "update_ticket"},
    "for other arguments": {"args_hash": "0" * 64},
    "for another requester": {"requester_oid": KAVITA.oid},
    "naming another approver": {"approver_oid": KAVITA.oid},
    "expired": {"expires_at": NOW},
}


@pytest.mark.parametrize("changed", WRONG_TOKENS.values(), ids=WRONG_TOKENS.keys())
async def test_a_token_that_does_not_match_the_stored_action_is_refused(
    bench: _Bench, changed: dict[str, Any]
) -> None:
    await bench.approved("issue_refund", GOODWILL)
    token = await _token(bench, **changed)

    refused = error_of(
        await bench.ask(ARJUN, "issue_refund", "key-1", approval_token=token, **GOODWILL)
    )

    assert (refused.code, refused.message) == (ErrorCode.APPROVAL_INVALID, REFUSED)
    assert bench.written.refunds == []


@pytest.mark.parametrize("token", ["", "not-a-token", "a.b.c"])
async def test_text_that_is_not_a_token_is_refused(bench: _Bench, token: str) -> None:
    await bench.approved("issue_refund", GOODWILL)

    refused = error_of(
        await bench.ask(ARJUN, "issue_refund", "key-1", approval_token=token, **GOODWILL)
    )

    assert refused.code is ErrorCode.APPROVAL_INVALID
    assert bench.written.refunds == []


async def test_a_token_signed_with_another_key_is_refused(bench: _Bench) -> None:
    from joserfc.jwk import ECKey

    await bench.approved("issue_refund", GOODWILL)
    other = LocalApprovalSigner(
        ECKey.generate_key("P-256", private=True).as_pem(private=True).decode()
    )
    action = bench.writes.actions.stored[0]
    forged = await other.sign(
        ApprovalClaims(
            token_id=uuid4(),
            requester_oid=ARJUN.oid,
            approver_oid=ARJUN.oid,
            action_id=action.id,
            tool="issue_refund",
            args_hash=action.new.args_hash,
            issued_at=NOW,
            expires_at=NOW + timedelta(seconds=120),
        )
    )

    refused = error_of(
        await bench.ask(ARJUN, "issue_refund", "key-1", approval_token=forged, **GOODWILL)
    )

    assert refused.code is ErrorCode.APPROVAL_INVALID
    assert bench.written.refunds == []


async def test_a_token_is_good_for_two_minutes(bench: _Bench) -> None:
    token = await bench.approved("issue_refund", GOODWILL)

    bench.now = NOW + timedelta(seconds=120)
    late = error_of(
        await bench.ask(ARJUN, "issue_refund", "key-1", approval_token=token, **GOODWILL)
    )
    action_id = bench.writes.actions.stored[0].id
    fresh = (await bench.writes.approvals.token(ARJUN, action_id)).token
    in_time = data_of(
        await bench.ask(ARJUN, "issue_refund", "key-1", approval_token=fresh, **GOODWILL)
    )

    assert late.code is ErrorCode.APPROVAL_INVALID
    assert in_time["status"] == "executed"


async def test_only_the_person_who_asked_can_carry_it_out(bench: _Bench) -> None:
    token = await bench.approved("issue_refund", FULL, by=KAVITA)

    by_approver = error_of(
        await bench.ask(KAVITA, "issue_refund", "key-1", approval_token=token, **FULL)
    )
    by_someone_without_the_right = error_of(
        await bench.ask(PRIYA, "issue_refund", "key-1", approval_token=token, **FULL)
    )

    assert by_approver.code is ErrorCode.APPROVAL_INVALID
    assert by_someone_without_the_right.code is ErrorCode.PERMISSION_DENIED
    assert bench.written.refunds == []


async def test_a_token_with_no_request_behind_it_is_refused(bench: _Bench) -> None:
    await bench.approved("issue_refund", GOODWILL)
    token = await _token(bench)

    refused = error_of(
        await bench.ask(ARJUN, "issue_refund", "another-key", approval_token=token, **GOODWILL)
    )

    assert refused.code is ErrorCode.APPROVAL_INVALID
    assert bench.written.refunds == []


async def test_an_action_that_was_only_asked_for_cannot_be_carried_out(bench: _Bench) -> None:
    data_of(await bench.ask(ARJUN, "issue_refund", "key-1", **GOODWILL))
    token = await _token(bench)

    refused = error_of(
        await bench.ask(ARJUN, "issue_refund", "key-1", approval_token=token, **GOODWILL)
    )

    assert refused.code is ErrorCode.APPROVAL_INVALID
    assert bench.status() == ["proposed"]


async def test_the_rules_are_applied_again_when_the_write_is_made(limits: Limits) -> None:
    bench = _Bench(limits)
    token = await bench.approved("issue_refund", GOODWILL)
    bench.written.refunds.append(
        RefundRow(
            id=900398,
            order_id=NEVER_SCANNED,
            region="west",
            amount_minor=60000,
            status="approved",
            pending_action_id=UUID(int=398),
        )
    )

    refused = error_of(
        await bench.ask(ARJUN, "issue_refund", "key-1", approval_token=token, **GOODWILL)
    )
    again = data_of(await bench.ask(ARJUN, "issue_refund", "key-1", **GOODWILL))

    assert (refused.code, refused.details["rule"]) == (
        ErrorCode.POLICY_VIOLATION,
        "goodwill_already_given",
    )
    assert len(bench.written.refunds) == 1
    assert bench.written.outbox == []
    assert bench.status() == ["failed"]
    assert again["status"] == "failed"


async def test_a_refund_that_now_needs_a_supervisor_is_not_made_on_a_self_approval(
    limits: Limits,
) -> None:
    bench = _Bench(limits)
    small = FULL | {"amount_minor": 400000}
    token = await bench.approved("issue_refund", small)
    bench.written.refunds.append(
        RefundRow(
            id=900397,
            order_id=LOSS_CONFIRMED,
            region="west",
            amount_minor=200000,
            status="paid",
            pending_action_id=UUID(int=397),
            reason_code="lost_in_transit",
        )
    )

    refused = error_of(
        await bench.ask(ARJUN, "issue_refund", "key-1", approval_token=token, **small)
    )

    assert refused.details == {"rule": "needs_supervisor"}
    assert bench.status() == ["failed"]


async def test_an_approved_note_and_status_change_are_written_to_the_ticket(bench: _Bench) -> None:
    update = NOTE | {"status": "pending_customer"}
    token = await bench.approved("update_ticket", update, asker=PRIYA)

    outcome = data_of(
        await bench.ask(PRIYA, "update_ticket", "key-1", approval_token=token, **update)
    )

    (note,) = bench.written.notes
    assert outcome["result"] == {
        "ticket_id": PLAIN,
        "status": "pending_customer",
        "queue": "L1",
        "version": 2,
    }
    assert (note.ticket_id, note.author_oid, note.author_name, note.body) == (
        PLAIN,
        PRIYA.oid,
        "Priya Nair",
        "Called the customer, no answer.",
    )


async def test_an_approved_escalation_moves_the_ticket_to_l2_with_its_recommendation(
    limits: Limits,
) -> None:
    tickets = tuple(
        replace(ticket, order_id=NEVER_SCANNED) if ticket.id == PLAIN else ticket
        for ticket in WORLD.tickets
    )
    bench = _Bench(limits, replace(WORLD, tickets=tickets))
    advice = {"amount_minor": 62000, "reason_code": "goodwill_delay", "reason_text": "80 hours."}
    update = {
        "ticket_id": PLAIN,
        "escalate_to": "L2",
        "note": "Needs a refund.",
        "recommendation": advice,
    }
    token = await bench.approved("update_ticket", update, asker=PRIYA)

    outcome = data_of(
        await bench.ask(PRIYA, "update_ticket", "key-1", approval_token=token, **update)
    )

    assert (outcome["result"]["queue"], outcome["result"]["status"]) == ("L2", "open")
    assert bench.written.escalations[PLAIN] == {"by_name": "Priya Nair", "recommendation": advice}
    assert len(bench.written.notes) == 1
    assert bench.written.refunds == []


async def test_a_ticket_that_changed_since_the_approval_is_left_alone(bench: _Bench) -> None:
    first = await bench.approved("update_ticket", NOTE, asker=PRIYA)
    second = await bench.approved(
        "update_ticket", NOTE | {"status": "resolved"}, asker=PRIYA, key="key-2"
    )
    await bench.ask(PRIYA, "update_ticket", "key-1", approval_token=first, **NOTE)

    refused = error_of(
        await bench.ask(
            PRIYA,
            "update_ticket",
            "key-2",
            approval_token=second,
            **(NOTE | {"status": "resolved"}),
        )
    )

    assert refused.details == {"rule": "ticket_changed"}
    assert len(bench.written.notes) == 1
    assert bench.written.tickets[PLAIN].version == 2
    assert bench.status() == ["executed", "failed"]


async def test_a_write_is_audited_once_as_an_execution_and_the_token_is_never_recorded(
    bench: _Bench,
) -> None:
    token = await bench.approved("issue_refund", GOODWILL)
    before = len(bench.audit.entries)

    with structlog.testing.capture_logs() as logs:
        await bench.ask(ARJUN, "issue_refund", "key-1", approval_token=token, **GOODWILL)
        await bench.ask(ARJUN, "issue_refund", "key-1", approval_token=token, **GOODWILL)
        await bench.ask(
            ARJUN, "issue_refund", "key-1", approval_token=GARBAGE, **_changed(amount_minor=1)
        )

    recorded = [
        (entry.target, entry.action, entry.outcome, entry.error_code)
        for entry in bench.audit.entries[before:]
    ]
    assert recorded == [
        ("issue_refund", "execute", "ok", None),
        ("issue_refund", "execute", "ok", None),
        ("issue_refund", "execute", "error", "APPROVAL_INVALID"),
    ]
    assert [line["event"] for line in logs if line["event"] == "audit"] == ["audit"] * 3
    everything = str(logs) + "".join(entry.model_dump_json() for entry in bench.audit.entries)
    assert token not in everything
    assert token.split(".")[2] not in everything
    assert bench.audit.entries[before].args_redacted["approval_token"] == KEPT_AS


async def test_a_write_the_rules_refuse_is_audited_once_with_the_refusal(limits: Limits) -> None:
    bench = _Bench(limits)
    token = await bench.approved("issue_refund", GOODWILL)
    bench.written.refunds.append(
        RefundRow(
            id=900396,
            order_id=NEVER_SCANNED,
            region="west",
            amount_minor=1,
            status="approved",
            pending_action_id=UUID(int=396),
        )
    )
    before = len(bench.audit.entries)

    await bench.ask(ARJUN, "issue_refund", "key-1", approval_token=token, **GOODWILL)

    (entry,) = bench.audit.entries[before:]
    assert (entry.action, entry.outcome, entry.error_code) == (
        "execute",
        "error",
        "POLICY_VIOLATION",
    )


async def test_nothing_is_written_when_the_record_of_it_cannot_be(bench: _Bench) -> None:
    token = await bench.approved("issue_refund", GOODWILL)
    bench.audit.down = True

    refused = error_of(
        await bench.ask(ARJUN, "issue_refund", "key-1", approval_token=token, **GOODWILL)
    )
    nothing_written = (list(bench.written.refunds), bench.status())
    bench.audit.down = False
    retried = data_of(
        await bench.ask(ARJUN, "issue_refund", "key-1", approval_token=token, **GOODWILL)
    )

    assert refused.code is ErrorCode.INTERNAL
    assert nothing_written == ([], ["approved"])
    assert retried["status"] == "executed"
    assert (len(bench.written.refunds), len(bench.written.outbox)) == (1, 1)


async def test_an_outage_during_the_write_leaves_the_approval_usable(bench: _Bench) -> None:
    token = await bench.approved("issue_refund", GOODWILL)
    (stored,) = bench.writes.actions.stored

    async def breaks(_action: object, _ledger: object) -> dict[str, Any]:
        raise BackendUnavailableError("The store is not answering.")

    with pytest.raises(BackendUnavailableError):
        await bench.writes.executions.run(
            Claim(
                action_id=stored.id,
                token_id=uuid4(),
                approver_oid=ARJUN.oid,
                args_hash=stored.new.args_hash,
                scope=scope_of(ARJUN),
                order_id=NEVER_SCANNED,
                now=NOW,
            ),
            breaks,
            lambda _refusal: _entry(),
        )

    assert (bench.status(), bench.written.used_tokens) == (["approved"], set())
    outcome = data_of(
        await bench.ask(ARJUN, "issue_refund", "key-1", approval_token=token, **GOODWILL)
    )
    assert outcome["status"] == "executed"

"""Carrying out an approved action in memory, with the same all-or-nothing rule."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from opscp_common.errors import OpsError
from opscp_common.hashing import JsonValue
from opscp_mcp.ports.actions import RefundCase
from opscp_mcp.ports.controls import AuditTrail
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
    is_refusal_by_the_rules,
)
from opscp_mcp.ports.meridian import Scope
from opscp_mcp_testing.fakes.actions import FakePendingActions, FakeRefundCases, StoredAction
from opscp_mcp_testing.world import RefundRow, World

FIRST_REFUND_ID = 950001


@dataclass(slots=True)
class Written:
    """Everything fake executions have changed, readable by a test."""

    refunds: list[RefundRow] = field(default_factory=list[RefundRow])
    outbox: list[dict[str, JsonValue]] = field(default_factory=list[dict[str, JsonValue]])
    notes: list[NewNote] = field(default_factory=list[NewNote])
    tickets: dict[int, TicketState] = field(default_factory=dict[int, TicketState])
    escalations: dict[int, dict[str, JsonValue]] = field(
        default_factory=dict[int, dict[str, JsonValue]]
    )
    used_tokens: set[UUID] = field(default_factory=set[UUID])


class _Ledger:
    def __init__(self, store: "FakeExecutions", scope: Scope, action: ApprovedAction) -> None:
        self._store = store
        self._scope = scope
        self._action = action

    async def refund_case(self, order_id: int) -> RefundCase | None:
        return await self._store.cases.for_order(self._scope, order_id, now=self._store.now())

    async def ticket(self, ticket_id: int) -> TicketState | None:
        changed = self._store.written.tickets.get(ticket_id)
        if changed is not None:
            return changed
        row = next((row for row in self._store.world.tickets if row.id == ticket_id), None)
        if row is None or self._scope.region not in ("*", row.region):
            return None
        return TicketState(
            id=row.id,
            order_id=row.order_id,
            region=row.region,
            status=row.status,
            queue=row.queue,
            version=row.version,
        )

    async def refund_unfinished_on(self, ticket_id: int) -> bool:
        return await self._store.cases.unfinished_on_ticket(
            self._scope, ticket_id, now=self._store.now()
        )

    async def add_refund(self, refund: NewRefund) -> int:
        written = self._store.written
        refund_id = FIRST_REFUND_ID + len(written.refunds)
        written.refunds.append(
            RefundRow(
                id=refund_id,
                order_id=refund.order_id,
                region=refund.region,
                amount_minor=refund.amount_minor,
                status="approved",
                pending_action_id=self._action.id,
                ticket_id=refund.ticket_id,
                reason_code=refund.reason_code,
                requested_by_name=self._action.requester_name,
                approved_by_name=self._action.approver_name,
            )
        )
        written.outbox.append(
            {
                "kind": "refund.submit",
                "refund_id": refund_id,
                "amount_minor": refund.amount_minor,
                "order_id": refund.order_id,
                "reason_code": refund.reason_code,
            }
        )
        return refund_id

    async def change_ticket(self, change: TicketChange) -> TicketState | None:
        ticket = await self.ticket(change.ticket_id)
        if ticket is None or ticket.version != change.from_version:
            return None
        changed = ticket.model_copy(
            update={
                "status": change.status or ticket.status,
                "queue": "L2" if change.escalation is not None else ticket.queue,
                "version": ticket.version + 1,
            }
        )
        self._store.written.tickets[ticket.id] = changed
        if change.escalation is not None:
            self._store.written.escalations[ticket.id] = {
                "by_name": change.escalation.by_name,
                "recommendation": change.escalation.recommendation,
            }
        return changed

    async def add_note(self, note: NewNote) -> None:
        self._store.written.notes.append(note)


class FakeExecutions:
    def __init__(
        self,
        world: World,
        actions: FakePendingActions,
        audit: AuditTrail,
        now: Callable[[], datetime],
    ) -> None:
        self.world = world
        self.written = Written()
        self.cases = FakeRefundCases(world, actions, self.written.refunds)
        self.now = now
        self._actions = actions
        self._audit = audit

    async def run(self, claim: Claim, work: Work, entry: EntryFor) -> Executed | None:
        stored = self._claimed(claim)
        if stored is None or stored.approver_oid is None or stored.approver_name is None:
            return None
        stored.status = "executed"
        self.written.used_tokens.add(claim.token_id)
        action = ApprovedAction(
            id=stored.id,
            tool=stored.new.tool,
            args=stored.new.args,
            approval_kind=stored.new.approval_kind,
            requester_oid=stored.new.requester_oid,
            requester_name=stored.new.requester_name,
            approver_oid=stored.approver_oid,
            approver_name=stored.approver_name,
            policy_snapshot=stored.new.policy_snapshot,
        )
        before = self._copy()
        try:
            result = await work(action, _Ledger(self, claim.scope, action))
            audit_id = await self._audit.record(entry(None))
        except OpsError as refusal:
            self._restore(before)
            if not is_refusal_by_the_rules(refusal):
                stored.status = "approved"
                self.written.used_tokens.discard(claim.token_id)
                raise
            body = refusal.body()
            stored.status = "failed"
            stored.result = {"code": body.code.value, "details": body.details}
            audit_id = await self._audit.record(entry(refusal))
            raise RefusedAtExecutionError(refusal, audit_id) from None
        except BaseException:
            self._restore(before)
            stored.status = "approved"
            self.written.used_tokens.discard(claim.token_id)
            raise
        stored.result = result
        return Executed(result=result, audit_id=audit_id)

    def _claimed(self, claim: Claim) -> StoredAction | None:
        found = next((a for a in self._actions.stored if a.id == claim.action_id), None)
        holds = (
            found is not None
            and found.status == "approved"
            and found.expires_at > claim.now
            and found.new.args_hash == claim.args_hash
            and found.new.requester_oid == claim.scope.user_oid
            and found.approver_oid == claim.approver_oid
            and claim.token_id not in self.written.used_tokens
        )
        return found if holds else None

    def _copy(
        self,
    ) -> tuple[int, int, int, dict[int, TicketState], dict[int, dict[str, JsonValue]]]:
        written = self.written
        return (
            len(written.refunds),
            len(written.outbox),
            len(written.notes),
            dict(written.tickets),
            dict(written.escalations),
        )

    def _restore(
        self, before: tuple[int, int, int, dict[int, TicketState], dict[int, dict[str, JsonValue]]]
    ) -> None:
        written = self.written
        del written.refunds[before[0] :]
        del written.outbox[before[1] :]
        del written.notes[before[2] :]
        written.tickets = before[3]
        written.escalations = before[4]

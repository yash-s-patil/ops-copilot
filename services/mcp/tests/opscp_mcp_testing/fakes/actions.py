"""Pending actions and refund facts held in memory, behaving like the database's."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from opscp_common.hashing import JsonValue
from opscp_common.vocabulary import ISSUE_REFUND, REFUND_UNSETTLED, SHIPPED
from opscp_mcp.ports.actions import (
    ActionRecord,
    ActionStatus,
    ActionsWanted,
    Decided,
    Decision,
    NewAction,
    RefundCase,
    WriteTool,
)
from opscp_mcp.ports.controls import AuditEntry, AuditTrail
from opscp_mcp.ports.meridian import Scope
from opscp_mcp_testing.fakes.controls import FakeAuditTrail
from opscp_mcp_testing.fakes.meridian import last_new_scan_at
from opscp_mcp_testing.world import EventRow, RefundRow, World, counts_against_its_order

_LIVE = ("proposed", "approved")


@dataclass(slots=True)
class StoredAction:
    id: UUID
    new: NewAction
    created_at: datetime
    expires_at: datetime
    status: ActionStatus = "proposed"
    approver_oid: UUID | None = None
    approver_name: str | None = None
    decided_at: datetime | None = None
    reason: str | None = None
    result: dict[str, JsonValue] | None = None


class FakePendingActions:
    def __init__(self, audit: AuditTrail | None = None) -> None:
        """`audit` is where the entry stored with a decision goes, as one transaction would."""
        self.stored: list[StoredAction] = []
        self._audit = audit or FakeAuditTrail()

    async def find(
        self, scope: Scope, tool: WriteTool, idempotency_key: str
    ) -> ActionRecord | None:
        found = self._find(scope.user_oid, tool, idempotency_key)
        return None if found is None else _record(found)

    async def propose(self, new: NewAction, *, now: datetime) -> ActionRecord | None:
        found = self._find(new.requester_oid, new.tool, new.idempotency_key)
        if found is None:
            if new.order_id is not None and self.refund_waiting(now, order_id=new.order_id):
                return None
            found = StoredAction(id=uuid4(), new=new, created_at=now, expires_at=new.expires_at)
            self.stored.append(found)
        return _record(found)

    async def get(self, action_id: UUID) -> ActionRecord | None:
        found = next((action for action in self.stored if action.id == action_id), None)
        return None if found is None else _record(found)

    async def decide(
        self, action_id: UUID, decision: Decision, entry: AuditEntry
    ) -> Decided | None:
        found = next((action for action in self.stored if action.id == action_id), None)
        if (
            found is None
            or found.status != "proposed"
            or found.expires_at <= decision.decided_at
            or found.new.args_hash != decision.seen_hash
        ):
            return None
        audit_id = await self._audit.record(entry)
        found.status = decision.status
        found.approver_oid = decision.approver_oid
        found.approver_name = decision.approver_name
        found.decided_at = decision.decided_at
        found.reason = decision.reason
        found.expires_at = decision.expires_at
        return Decided(action=_record(found), audit_id=audit_id)

    async def waiting(self, wanted: ActionsWanted, *, now: datetime) -> tuple[ActionRecord, ...]:
        def visible(action: StoredAction) -> bool:
            mine = action.new.requester_oid == wanted.requester_oid
            region = wanted.awaiting_supervisor_in
            theirs = (
                region is not None
                and action.new.approval_kind == "supervisor"
                and action.status == "proposed"
                and region in ("*", action.new.requester_region)
            )
            return (
                (mine or theirs)
                and action.status in _LIVE
                and action.expires_at > now
                and wanted.ticket_id in (None, action.new.ticket_id)
            )

        newest_first = sorted(self.stored, key=lambda action: action.created_at, reverse=True)
        found = [_record(action) for action in newest_first if visible(action)]
        return tuple(found[: wanted.limit])

    def refund_waiting(
        self, now: datetime, *, order_id: int | None = None, ticket_id: int | None = None
    ) -> bool:
        return any(
            action.new.tool == ISSUE_REFUND
            and action.status in _LIVE
            and action.expires_at > now
            and (order_id is None or action.new.order_id == order_id)
            and (ticket_id is None or action.new.ticket_id == ticket_id)
            for action in self.stored
        )

    def _find(self, requester_oid: UUID, tool: str, idempotency_key: str) -> StoredAction | None:
        return next(
            (
                action
                for action in self.stored
                if (action.new.requester_oid, action.new.tool, action.new.idempotency_key)
                == (requester_oid, tool, idempotency_key)
            ),
            None,
        )


def _record(action: StoredAction) -> ActionRecord:
    return ActionRecord(
        id=action.id,
        tool=action.new.tool,
        args=action.new.args,
        args_hash=action.new.args_hash,
        requester_oid=action.new.requester_oid,
        requester_name=action.new.requester_name,
        requester_region=action.new.requester_region,
        approval_kind=action.new.approval_kind,
        status=action.status,
        approver_oid=action.approver_oid,
        approver_name=action.approver_name,
        decided_at=action.decided_at,
        decision_reason=action.reason,
        policy_snapshot=action.new.policy_snapshot,
        ticket_id=action.new.ticket_id,
        result=action.result,
        expires_at=action.expires_at,
        created_at=action.created_at,
    )


class FakeRefundCases:
    def __init__(
        self, world: World, actions: FakePendingActions, written: list[RefundRow] | None = None
    ) -> None:
        """`written` holds refunds made since the world was built, by a fake execution."""
        self._world = world
        self._actions = actions
        self._written = [] if written is None else written
        self.changed: dict[int, RefundRow] = {}
        """Refunds of the world as a fake retry left them."""

    async def for_order(
        self, scope: Scope, order_id: int, *, now: datetime, leaving_out: int | None = None
    ) -> RefundCase | None:
        order = next((row for row in self._world.orders if row.id == order_id), None)
        if order is None or scope.region not in ("*", order.region):
            return None
        events = sorted(
            (event for event in self._world.events if event.order_id == order_id),
            key=lambda event: event.occurred_at,
        )
        refunds = [
            refund
            for refund in (
                *(self.changed.get(row.id, row) for row in self._world.refunds),
                *self._written,
            )
            if refund.order_id == order_id
            and refund.id != leaving_out
            and counts_against_its_order(refund)
        ]
        customer = next(row for row in self._world.customers if row.id == order.customer_id)
        return RefundCase(
            order_id=order.id,
            region=order.region,
            tier=customer.tier,
            status=order.status,
            status_changed_at=order.status_changed_at,
            amount_minor=order.amount_minor,
            refunded_minor=sum(refund.amount_minor for refund in refunds),
            last_new_scan_at=last_new_scan_at(events) if order.status == SHIPPED else None,
            carrier_exception_at=_latest(events, "carrier_exception"),
            loss_confirmed=_latest(events, "lost_in_transit_confirmed") is not None,
            damage_reported=_latest(events, "damage_reported") is not None,
            goodwill_given=any(refund.reason_code == "goodwill_delay" for refund in refunds),
            request_waiting=self._waiting_in_world(order_id, now)
            or self._actions.refund_waiting(now, order_id=order_id),
        )

    def _waiting_in_world(self, order_id: int, now: datetime) -> bool:
        """The world's own requests name a ticket or a refund; either leads to the order."""
        order_of_ticket = {ticket.id: ticket.order_id for ticket in self._world.tickets}
        order_of_refund = {
            refund.pending_action_id: refund.order_id for refund in self._world.refunds
        }
        return any(
            action.tool == ISSUE_REFUND
            and action.status in _LIVE
            and action.expires_at > now
            and order_id
            in (order_of_refund.get(action.id), order_of_ticket.get(action.ticket_id or 0))
            for action in self._world.pending_actions
        )

    async def unfinished_on_ticket(self, scope: Scope, ticket_id: int, *, now: datetime) -> bool:
        unsettled = any(
            refund.ticket_id == ticket_id and refund.status in REFUND_UNSETTLED
            for refund in (
                *(self.changed.get(row.id, row) for row in self._world.refunds),
                *self._written,
            )
        )
        waiting = any(
            action.ticket_id == ticket_id
            and action.tool == ISSUE_REFUND
            and action.status in _LIVE
            and action.expires_at > now
            for action in self._world.pending_actions
        )
        return unsettled or waiting or self._actions.refund_waiting(now, ticket_id=ticket_id)


def _latest(events: list[EventRow], kind: str) -> datetime | None:
    return max((event.occurred_at for event in events if event.event_type == kind), default=None)

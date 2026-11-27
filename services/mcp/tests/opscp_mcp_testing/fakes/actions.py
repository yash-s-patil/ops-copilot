"""Pending actions and refund facts held in memory, behaving like the database's."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from opscp_common.vocabulary import ISSUE_REFUND, REFUND_FAILED, REFUND_UNSETTLED, SHIPPED
from opscp_mcp.ports.actions import (
    ActionRecord,
    ActionStatus,
    NewAction,
    RefundCase,
    WriteTool,
)
from opscp_mcp.ports.meridian import Scope
from opscp_mcp_testing.fakes.meridian import last_new_scan_at
from opscp_mcp_testing.world import EventRow, World

_LIVE = ("proposed", "approved")


@dataclass(slots=True)
class StoredAction:
    id: UUID
    new: NewAction
    status: ActionStatus = "proposed"


class FakePendingActions:
    def __init__(self) -> None:
        self.stored: list[StoredAction] = []

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
            found = StoredAction(id=uuid4(), new=new)
            self.stored.append(found)
        return _record(found)

    def refund_waiting(
        self, now: datetime, *, order_id: int | None = None, ticket_id: int | None = None
    ) -> bool:
        return any(
            action.new.tool == ISSUE_REFUND
            and action.status in _LIVE
            and action.new.expires_at > now
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
        approval_kind=action.new.approval_kind,
        status=action.status,
        expires_at=action.new.expires_at,
    )


class FakeRefundCases:
    def __init__(self, world: World, actions: FakePendingActions) -> None:
        self._world = world
        self._actions = actions

    async def for_order(self, scope: Scope, order_id: int, *, now: datetime) -> RefundCase | None:
        order = next((row for row in self._world.orders if row.id == order_id), None)
        if order is None or scope.region not in ("*", order.region):
            return None
        events = sorted(
            (event for event in self._world.events if event.order_id == order_id),
            key=lambda event: event.occurred_at,
        )
        refunds = [
            refund
            for refund in self._world.refunds
            if refund.order_id == order_id and refund.status != REFUND_FAILED
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
            for refund in self._world.refunds
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

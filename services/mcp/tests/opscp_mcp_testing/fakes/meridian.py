"""In-memory stand-ins for the meridian ports, scoped by region as row-level security is."""

from collections.abc import Callable
from datetime import datetime

from opscp_common.vocabulary import (
    ALL_REGIONS,
    CARRIER_SCAN,
    OPEN_TICKET_STATUSES,
    REFUND_FAILED,
    SHIPPED,
    EventType,
    Region,
)
from opscp_mcp.ports.meridian import CustomerRecord, EventRecord, OrderRecord, Scope
from opscp_mcp_testing.world import CustomerRow, EventRow, World


def _visible(scope: Scope, region: Region) -> bool:
    return scope.region in (ALL_REGIONS, region)


class FakeCustomers:
    def __init__(self, world: World) -> None:
        self._world = world

    async def by_id(self, scope: Scope, customer_id: int) -> CustomerRecord | None:
        return self._first(scope, lambda row: row.id == customer_id)

    async def by_email(self, scope: Scope, email: str) -> CustomerRecord | None:
        return self._first(scope, lambda row: row.email.lower() == email.lower())

    def _first(self, scope: Scope, matches: Callable[[CustomerRow], bool]) -> CustomerRecord | None:
        for row in self._world.customers:
            if matches(row) and _visible(scope, row.region):
                return CustomerRecord(
                    id=row.id,
                    full_name=row.full_name,
                    email=row.email,
                    phone=row.phone,
                    tier=row.tier,
                    region=row.region,
                    created_at=row.created_at,
                    open_ticket_count=sum(
                        ticket.customer_id == row.id and ticket.status in OPEN_TICKET_STATUSES
                        for ticket in self._world.tickets
                    ),
                )
        return None


class FakeOrders:
    def __init__(self, world: World) -> None:
        self._world = world

    async def get(self, scope: Scope, order_id: int, *, recent_events: int) -> OrderRecord | None:
        row = next((order for order in self._world.orders if order.id == order_id), None)
        if row is None or not _visible(scope, row.region):
            return None
        events = sorted(
            (event for event in self._world.events if event.order_id == order_id),
            key=lambda event: event.occurred_at,
        )
        counts: dict[EventType, int] = {}
        for event in events:
            counts[event.event_type] = counts.get(event.event_type, 0) + 1
        return OrderRecord(
            id=row.id,
            customer_id=row.customer_id,
            status=row.status,
            amount_minor=row.amount_minor,
            currency=row.currency,
            region=row.region,
            carrier=row.carrier,
            tracking_ref=row.tracking_ref,
            placed_at=row.placed_at,
            status_changed_at=row.status_changed_at,
            refunded_minor=sum(
                refund.amount_minor
                for refund in self._world.refunds
                if refund.order_id == order_id and refund.status != REFUND_FAILED
            ),
            last_new_scan_at=_last_new_scan_at(events) if row.status == SHIPPED else None,
            recent_events=tuple(
                EventRecord(
                    type=event.event_type, occurred_at=event.occurred_at, payload=event.payload
                )
                for event in reversed(events[len(events) - min(recent_events, len(events)) :])
            ),
            event_counts=counts if recent_events else {},
        )


def _last_new_scan_at(events: list[EventRow]) -> datetime | None:
    scans = [event for event in events if event.event_type == CARRIER_SCAN]
    new = [
        scan.occurred_at
        for index, scan in enumerate(scans)
        if index == 0 or scan.payload != scans[index - 1].payload
    ]
    return max(new, default=None)

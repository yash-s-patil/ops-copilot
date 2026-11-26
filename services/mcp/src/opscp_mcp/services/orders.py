from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Literal

from opscp_common.errors import NotFoundError
from opscp_common.limits import Limits, StuckLimits
from opscp_common.vocabulary import EventType, OrderStatus
from opscp_mcp.authz.identity import Identity
from opscp_mcp.authz.policy import Permission
from opscp_mcp.ports.meridian import EventRecord, OrderRecord, Orders
from opscp_mcp.services.caps import Result, cap_text
from opscp_mcp.services.inputs import require_id
from opscp_mcp.services.scope import scope_of

type Clock = Callable[[], datetime]
type EventDetail = Literal["recent", "none"]

_HOUR = timedelta(hours=1)
_LABELS: Mapping[EventType, str] = {
    "payment_hold": "Payment hold",
    "payment_released": "Payment released",
    "address_validation_failed": "Address validation failed",
    "packed": "Packed",
    "carrier_pickup": "Carrier pickup",
    "carrier_scan": "Carrier scan",
    "carrier_exception": "Carrier exception",
    "lost_in_transit_confirmed": "Loss in transit confirmed",
    "damage_reported": "Damage reported",
    "delivered": "Delivered",
    "cancelled": "Cancelled",
}

_DETAILS: Mapping[EventType, tuple[str, ...]] = {
    "payment_hold": ("reason",),
    "payment_released": (),
    "address_validation_failed": ("reason",),
    "packed": ("warehouse",),
    "carrier_pickup": ("carrier", "hub"),
    "carrier_scan": ("hub", "scan"),
    "carrier_exception": ("reason", "hub"),
    "lost_in_transit_confirmed": ("confirmed_by",),
    "damage_reported": ("detail",),
    "delivered": ("received_by",),
    "cancelled": ("reason",),
}


class EventView(Result):
    type: EventType
    occurred_at: datetime
    summary: str
    truncated: bool


class OrderEvents(Result):
    recent: tuple[EventView, ...]
    total: int
    older_by_type: dict[EventType, int]


class OrderView(Result):
    order_id: int
    customer_id: int
    status: OrderStatus
    status_age_hours: int
    is_stuck: bool
    amount_minor: int
    currency: str
    refunded_minor: int
    placed_at: datetime
    carrier: str | None
    tracking_ref: str | None
    events: OrderEvents | None


class OrderService:
    def __init__(self, orders: Orders, limits: Limits, clock: Clock) -> None:
        self._orders = orders
        self._limits = limits
        self._clock = clock

    async def get(
        self, identity: Identity, order_id: int, *, events: EventDetail = "recent"
    ) -> OrderView:
        identity.require(Permission.ORDERS_READ)
        shown = self._limits.events.recent if events == "recent" else 0
        record = await self._orders.get(
            scope_of(identity), require_id(order_id, "order_id"), recent_events=shown
        )
        if record is None:
            raise NotFoundError("No order with that number is visible to you.")
        now = self._clock()
        return OrderView(
            order_id=record.id,
            customer_id=record.customer_id,
            status=record.status,
            status_age_hours=max((now - record.status_changed_at) // _HOUR, 0),
            is_stuck=is_stuck(record, now, self._limits.stuck),
            amount_minor=record.amount_minor,
            currency=record.currency,
            refunded_minor=record.refunded_minor,
            placed_at=record.placed_at,
            carrier=record.carrier,
            tracking_ref=record.tracking_ref,
            events=self._events(record) if events == "recent" else None,
        )

    def _events(self, record: OrderRecord) -> OrderEvents:
        older = dict(record.event_counts)
        for event in record.recent_events:
            older[event.type] -= 1
        return OrderEvents(
            recent=tuple(self._event(event) for event in record.recent_events),
            total=sum(record.event_counts.values()),
            older_by_type={kind: count for kind, count in older.items() if count > 0},
        )

    def _event(self, event: EventRecord) -> EventView:
        limits = self._limits.event
        summary = cap_text(summarise(event, limits.detail_chars), limits.summary_chars)
        return EventView(
            type=event.type,
            occurred_at=event.occurred_at,
            summary=summary.text,
            truncated=summary.truncated,
        )


def is_stuck(record: OrderRecord, now: datetime, limits: StuckLimits) -> bool:
    """Paid or packed for too long, or shipped with no new carrier scan for too long."""
    if record.status == "paid":
        return now - record.status_changed_at > limits.paid_hours * _HOUR
    if record.status == "packed":
        return now - record.status_changed_at > limits.packed_hours * _HOUR
    if record.status == "shipped":
        last_movement = record.last_new_scan_at or record.status_changed_at
        return now - last_movement > limits.shipped_no_scan_hours * _HOUR
    return False


def summarise(event: EventRecord, detail_chars: int) -> str:
    """The event in words with its known details: "Carrier scan (hub: PNQ-2, scan: arrived)".

    Payloads come from carriers and other outside systems. Only the details expected for the
    event type are shown, each on one line and cut short, so a payload cannot add fields,
    reorder them or carry a paragraph.
    """
    shown = (
        f"{key}: {_one_line(value, detail_chars)}"
        for key in _DETAILS[event.type]
        if isinstance(value := event.payload.get(key), str | int) and not isinstance(value, bool)
    )
    details = ", ".join(shown)
    label = _LABELS[event.type]
    return f"{label} ({details})" if details else label


def _one_line(value: str | int, max_chars: int) -> str:
    printable = "".join(character if character.isprintable() else " " for character in str(value))
    return " ".join(printable.replace(",", " ").split())[:max_chars]

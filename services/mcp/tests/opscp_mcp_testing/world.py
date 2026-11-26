"""A small, fixed set of rows that the in-memory fakes and the test database both hold."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import JsonValue

from opscp_common.vocabulary import EventType, OrderStatus, Region, TicketStatus, Tier

NOW = datetime(2026, 11, 28, 4, 30, tzinfo=UTC)


def ago(hours: float) -> datetime:
    return NOW - timedelta(hours=hours)


_LONG_AGO = ago(24 * 400)
_LAST_MONTH = ago(24 * 30)


@dataclass(frozen=True, slots=True, kw_only=True)
class CustomerRow:
    id: int
    full_name: str
    email: str
    phone: str | None
    tier: Tier
    region: Region
    created_at: datetime = _LONG_AGO


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderRow:
    id: int
    customer_id: int
    status: OrderStatus
    region: Region
    status_changed_at: datetime
    amount_minor: int = 620000
    currency: str = "INR"
    carrier: str | None = None
    tracking_ref: str | None = None
    placed_at: datetime = _LAST_MONTH


@dataclass(frozen=True, slots=True, kw_only=True)
class EventRow:
    order_id: int
    event_type: EventType
    occurred_at: datetime
    payload: dict[str, JsonValue] = field(default_factory=dict[str, JsonValue])


@dataclass(frozen=True, slots=True, kw_only=True)
class TicketRow:
    id: int
    order_id: int
    customer_id: int
    region: Region
    status: TicketStatus


@dataclass(frozen=True, slots=True, kw_only=True)
class RefundRow:
    id: int
    order_id: int
    region: Region
    amount_minor: int
    status: str
    pending_action_id: UUID


@dataclass(frozen=True, slots=True, kw_only=True)
class World:
    customers: tuple[CustomerRow, ...]
    orders: tuple[OrderRow, ...]
    events: tuple[EventRow, ...]
    tickets: tuple[TicketRow, ...]
    refunds: tuple[RefundRow, ...]


ASHA, BALA = 900001, 900002
NOBODY, NO_ORDER = 999999001, 999999101
STUCK_ON_ADDRESS, SOUTH_ORDER, PAYMENT_HELD = 900101, 900102, 900103
SCANNER_LOOP, MOVING, NEVER_SCANNED, DELIVERED = 900104, 900105, 900106, 900107

_FAILED_VALIDATION: dict[str, JsonValue] = {"reason": "postcode_mismatch"}
_LOOPING_SCAN: dict[str, JsonValue] = {"hub": "PNQ-2", "scan": "arrived"}
_LOOP_HOURS_AGO = (100, 91, 82, 73, 64, 55, 46, 37, 28, 19, 10, 1)

WORLD = World(
    customers=(
        CustomerRow(
            id=ASHA,
            full_name="Asha Rao",
            email="asha.rao@example.in",
            phone="+91 58200 00001",
            tier="premium",
            region="west",
        ),
        CustomerRow(
            id=BALA,
            full_name="Bala Nair",
            email="bala.nair@example.in",
            phone=None,
            tier="standard",
            region="south",
        ),
    ),
    orders=(
        OrderRow(
            id=STUCK_ON_ADDRESS,
            customer_id=ASHA,
            status="packed",
            region="west",
            status_changed_at=ago(96.5),
        ),
        OrderRow(
            id=SOUTH_ORDER,
            customer_id=BALA,
            status="packed",
            region="south",
            status_changed_at=ago(60),
        ),
        OrderRow(
            id=PAYMENT_HELD,
            customer_id=ASHA,
            status="paid",
            region="west",
            status_changed_at=ago(30),
        ),
        OrderRow(
            id=SCANNER_LOOP,
            customer_id=ASHA,
            status="shipped",
            region="west",
            status_changed_at=ago(130),
            carrier="BlueArrow Logistics",
            tracking_ref="BA0000000001",
        ),
        OrderRow(
            id=MOVING, customer_id=ASHA, status="shipped", region="west", status_changed_at=ago(80)
        ),
        OrderRow(
            id=NEVER_SCANNED,
            customer_id=ASHA,
            status="shipped",
            region="west",
            status_changed_at=ago(80),
        ),
        OrderRow(
            id=DELIVERED,
            customer_id=ASHA,
            status="delivered",
            region="west",
            status_changed_at=ago(500),
        ),
    ),
    events=(
        EventRow(order_id=STUCK_ON_ADDRESS, event_type="payment_released", occurred_at=ago(120)),
        EventRow(
            order_id=STUCK_ON_ADDRESS,
            event_type="packed",
            occurred_at=ago(96.5),
            payload={"warehouse": "WH-PNQ"},
        ),
        *(
            EventRow(
                order_id=STUCK_ON_ADDRESS,
                event_type="address_validation_failed",
                occurred_at=ago(hours),
                payload=_FAILED_VALIDATION,
            )
            for hours in (95, 71, 47, 23)
        ),
        EventRow(
            order_id=PAYMENT_HELD,
            event_type="payment_hold",
            occurred_at=ago(30),
            payload={"reason": "risk_review"},
        ),
        EventRow(
            order_id=SCANNER_LOOP,
            event_type="carrier_pickup",
            occurred_at=ago(130),
            payload={"carrier": "BlueArrow Logistics", "hub": "BOM-1"},
        ),
        *(
            EventRow(
                order_id=SCANNER_LOOP,
                event_type="carrier_scan",
                occurred_at=ago(hours),
                payload=_LOOPING_SCAN,
            )
            for hours in _LOOP_HOURS_AGO
        ),
        EventRow(order_id=MOVING, event_type="carrier_pickup", occurred_at=ago(80)),
        EventRow(
            order_id=MOVING,
            event_type="carrier_scan",
            occurred_at=ago(60),
            payload={"hub": "BOM-1", "scan": "arrived"},
        ),
        EventRow(
            order_id=MOVING,
            event_type="carrier_scan",
            occurred_at=ago(10),
            payload={"hub": "BOM-1", "scan": "departed"},
        ),
        EventRow(order_id=NEVER_SCANNED, event_type="carrier_pickup", occurred_at=ago(80)),
    ),
    tickets=(
        TicketRow(
            id=900201, order_id=STUCK_ON_ADDRESS, customer_id=ASHA, region="west", status="open"
        ),
        TicketRow(
            id=900202,
            order_id=PAYMENT_HELD,
            customer_id=ASHA,
            region="west",
            status="pending_customer",
        ),
        TicketRow(
            id=900203, order_id=DELIVERED, customer_id=ASHA, region="west", status="resolved"
        ),
        TicketRow(id=900204, order_id=SOUTH_ORDER, customer_id=BALA, region="south", status="open"),
    ),
    refunds=(
        RefundRow(
            id=900301,
            order_id=STUCK_ON_ADDRESS,
            region="west",
            amount_minor=10000,
            status="approved",
            pending_action_id=UUID("90000000-0000-4000-8000-000000000001"),
        ),
        RefundRow(
            id=900302,
            order_id=STUCK_ON_ADDRESS,
            region="west",
            amount_minor=5000,
            status="failed",
            pending_action_id=UUID("90000000-0000-4000-8000-000000000002"),
        ),
    ),
)

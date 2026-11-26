"""A small, fixed set of rows that the in-memory fakes and the test database both hold."""

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import JsonValue

from opscp_common.vocabulary import (
    AuthorKind,
    EventType,
    OrderStatus,
    Priority,
    Queue,
    RefundReason,
    RefundStatus,
    Region,
    TicketStatus,
    Tier,
)
from opscp_mcp_testing.identities import ARJUN, KAVITA, PRIYA

NOW = datetime(2026, 11, 28, 4, 30, tzinfo=UTC)


def ago(hours: float) -> datetime:
    return NOW - timedelta(hours=hours)


_LONG_AGO = ago(24 * 400)
_LAST_MONTH = ago(24 * 30)
_NEXT_DAY = ago(-20)


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
    updated_at: datetime = _LAST_MONTH
    priority: Priority = "P3"
    queue: Queue = "L1"
    summary: str = "A test ticket"
    escalated_by_oid: UUID | None = None
    escalated_by_name: str | None = None
    escalated_at: datetime | None = None
    recommendation: dict[str, JsonValue] | None = None
    version: int = 1


@dataclass(frozen=True, slots=True, kw_only=True)
class NoteRow:
    ticket_id: int
    author_kind: AuthorKind
    author_name: str
    body: str
    created_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class PendingActionRow:
    id: UUID
    region: Region
    status: str
    requester_oid: UUID = ARJUN.oid
    requester_name: str = ARJUN.name
    ticket_id: int | None = None
    tool: str = "issue_refund"
    approval_kind: str = "supervisor"
    approver_oid: UUID | None = None
    approver_name: str | None = None
    args: dict[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    expires_at: datetime = _NEXT_DAY
    created_at: datetime = _LAST_MONTH


@dataclass(frozen=True, slots=True, kw_only=True)
class RefundRow:
    id: int
    order_id: int
    region: Region
    amount_minor: int
    status: RefundStatus
    pending_action_id: UUID
    ticket_id: int | None = None
    reason_code: RefundReason = "goodwill_delay"
    requested_by_name: str = ARJUN.name
    approved_by_name: str = ARJUN.name
    failure_reason: str | None = None
    created_at: datetime = _LAST_MONTH


@dataclass(frozen=True, slots=True, kw_only=True)
class World:
    customers: tuple[CustomerRow, ...]
    orders: tuple[OrderRow, ...]
    events: tuple[EventRow, ...]
    tickets: tuple[TicketRow, ...]
    notes: tuple[NoteRow, ...]
    pending_actions: tuple[PendingActionRow, ...]
    refunds: tuple[RefundRow, ...]


ASHA, BALA, CHITRA = 900001, 900002, 900003
NOBODY, NO_ORDER, NO_TICKET = 999999001, 999999101, 999999201
STUCK_ON_ADDRESS, SOUTH_ORDER, PAYMENT_HELD = 900101, 900102, 900103
SCANNER_LOOP, MOVING, NEVER_SCANNED, DELIVERED = 900104, 900105, 900106, 900107
CHITRA_ORDER = 900108

PLAIN, WAITING_ON_CUSTOMER, RESOLVED, SOUTH_PLAIN = 900201, 900202, 900203, 900204
ESCALATED_BY_PRIYA, AWAITING_SUPERVISOR, SENT_TO_PAYMENTS = 900211, 900212, 900213
APPROVED_BY_SUPERVISOR, REQUEST_EXPIRED, REQUESTED_BY_KAVITA = 900214, 900215, 900216
SELF_APPROVAL_PENDING, PAYMENT_FAILED, PAID, CLOSED = 900217, 900218, 900219, 900220
SOUTH_AWAITING_SUPERVISOR, SOUTH_RESOLVED = 900231, 900232

NEHA_OID = UUID("90000000-0000-4000-8000-0000000000a1")
ANIL_OID = UUID("90000000-0000-4000-8000-0000000000a2")
LONG_NOTE = "पार्सल अभी तक नहीं आया। " * 40
FULL_REFUND: dict[str, JsonValue] = {"amount_minor": 890000, "reason_code": "lost_in_transit"}

_FAILED_VALIDATION: dict[str, JsonValue] = {"reason": "postcode_mismatch"}
_LOOPING_SCAN: dict[str, JsonValue] = {"hub": "PNQ-2", "scan": "arrived"}
_LOOP_HOURS_AGO = (100, 91, 82, 73, 64, 55, 46, 37, 28, 19, 10, 1)


def _action(number: int) -> UUID:
    return UUID(f"90000000-0000-4000-8000-{number:012d}")


def _chitra_ticket(ticket_id: int, hours: float, status: TicketStatus = "open") -> TicketRow:
    return TicketRow(
        id=ticket_id,
        order_id=CHITRA_ORDER,
        customer_id=CHITRA,
        region="west",
        status=status,
        queue="L2",
        updated_at=ago(hours),
        escalated_by_oid=NEHA_OID,
        escalated_by_name="Neha Pillai",
        escalated_at=ago(hours),
    )


def _executed(number: int, ticket_id: int | None = None) -> PendingActionRow:
    return PendingActionRow(
        id=_action(number),
        region="west",
        status="executed",
        ticket_id=ticket_id,
        approval_kind="self",
    )


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
        CustomerRow(
            id=CHITRA,
            full_name="Chitra Desai",
            email="chitra.desai@example.in",
            phone="+91 58200 00003",
            tier="standard",
            region="west",
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
            id=CHITRA_ORDER,
            customer_id=CHITRA,
            status="lost",
            region="west",
            status_changed_at=ago(40),
            amount_minor=890000,
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
            id=PLAIN,
            order_id=STUCK_ON_ADDRESS,
            customer_id=ASHA,
            region="west",
            status="open",
            updated_at=ago(1),
            priority="P2",
            summary="Order stuck for four days",
        ),
        TicketRow(
            id=WAITING_ON_CUSTOMER,
            order_id=PAYMENT_HELD,
            customer_id=ASHA,
            region="west",
            status="pending_customer",
            updated_at=ago(2),
        ),
        TicketRow(
            id=RESOLVED,
            order_id=DELIVERED,
            customer_id=ASHA,
            region="west",
            status="resolved",
            updated_at=ago(300),
        ),
        TicketRow(
            id=SOUTH_PLAIN,
            order_id=SOUTH_ORDER,
            customer_id=BALA,
            region="south",
            status="open",
            updated_at=ago(3),
        ),
        replace(
            _chitra_ticket(ESCALATED_BY_PRIYA, 4),
            escalated_by_oid=PRIYA.oid,
            escalated_by_name=PRIYA.name,
            recommendation={**FULL_REFUND, "reason_text": "Loss confirmed by the carrier"},
            version=2,
        ),
        _chitra_ticket(AWAITING_SUPERVISOR, 5),
        _chitra_ticket(SENT_TO_PAYMENTS, 6),
        _chitra_ticket(APPROVED_BY_SUPERVISOR, 7),
        _chitra_ticket(REQUEST_EXPIRED, 8),
        _chitra_ticket(REQUESTED_BY_KAVITA, 9),
        TicketRow(
            id=SELF_APPROVAL_PENDING,
            order_id=CHITRA_ORDER,
            customer_id=CHITRA,
            region="west",
            status="open",
            updated_at=ago(10),
        ),
        _chitra_ticket(PAYMENT_FAILED, 11),
        _chitra_ticket(PAID, 200, "resolved"),
        _chitra_ticket(CLOSED, 400, "closed"),
        TicketRow(
            id=SOUTH_AWAITING_SUPERVISOR,
            order_id=SOUTH_ORDER,
            customer_id=BALA,
            region="south",
            status="open",
            queue="L2",
            updated_at=ago(12),
            escalated_by_oid=ANIL_OID,
            escalated_by_name="Anil Varghese",
            escalated_at=ago(12),
        ),
        TicketRow(
            id=SOUTH_RESOLVED,
            order_id=SOUTH_ORDER,
            customer_id=BALA,
            region="south",
            status="resolved",
            updated_at=ago(250),
        ),
    ),
    notes=(
        NoteRow(
            ticket_id=ESCALATED_BY_PRIYA,
            author_kind="customer",
            author_name="Chitra Desai",
            body="The courier says they cannot find it.",
            created_at=ago(50),
        ),
        NoteRow(
            ticket_id=ESCALATED_BY_PRIYA,
            author_kind="engineer",
            author_name=PRIYA.name,
            body="Escalating to L2: loss confirmed and the customer wants a full refund.",
            created_at=ago(30),
        ),
        NoteRow(
            ticket_id=ESCALATED_BY_PRIYA,
            author_kind="customer",
            author_name="Chitra Desai",
            body=LONG_NOTE,
            created_at=ago(20),
        ),
        NoteRow(
            ticket_id=SOUTH_PLAIN,
            author_kind="customer",
            author_name="Bala Nair",
            body="When will this be dispatched?",
            created_at=ago(10),
        ),
    ),
    pending_actions=(
        _executed(1),
        _executed(2),
        PendingActionRow(
            id=_action(3),
            region="west",
            status="proposed",
            ticket_id=AWAITING_SUPERVISOR,
            args=FULL_REFUND,
        ),
        _executed(4, SENT_TO_PAYMENTS),
        PendingActionRow(
            id=_action(5),
            region="west",
            status="approved",
            ticket_id=APPROVED_BY_SUPERVISOR,
            args=FULL_REFUND,
            approver_oid=KAVITA.oid,
            approver_name=KAVITA.name,
        ),
        PendingActionRow(
            id=_action(6),
            region="west",
            status="proposed",
            ticket_id=REQUEST_EXPIRED,
            args=FULL_REFUND,
            expires_at=ago(1),
        ),
        PendingActionRow(
            id=_action(7),
            region="west",
            status="proposed",
            requester_oid=KAVITA.oid,
            requester_name=KAVITA.name,
            ticket_id=REQUESTED_BY_KAVITA,
            args=FULL_REFUND,
        ),
        PendingActionRow(
            id=_action(8),
            region="west",
            status="proposed",
            ticket_id=SELF_APPROVAL_PENDING,
            approval_kind="self",
            args={"amount_minor": 10000, "reason_code": "goodwill_delay"},
        ),
        PendingActionRow(
            id=_action(13),
            region="west",
            status="proposed",
            requester_oid=KAVITA.oid,
            requester_name=KAVITA.name,
            ticket_id=AWAITING_SUPERVISOR,
            args={"amount_minor": 1, "reason_code": "damaged"},
            created_at=ago(1),
        ),
        PendingActionRow(
            id=_action(14),
            region="west",
            status="approved",
            ticket_id=PAID,
            args=FULL_REFUND,
            approver_oid=KAVITA.oid,
            approver_name=KAVITA.name,
            expires_at=ago(1),
        ),
        _executed(9, PAYMENT_FAILED),
        _executed(10, PAID),
        PendingActionRow(
            id=_action(11),
            region="south",
            status="proposed",
            requester_oid=ANIL_OID,
            requester_name="Anil Varghese",
            ticket_id=SOUTH_AWAITING_SUPERVISOR,
            args=FULL_REFUND,
        ),
        PendingActionRow(
            id=_action(12),
            region="west",
            status="rejected",
            ticket_id=PLAIN,
            args=FULL_REFUND,
            approver_oid=KAVITA.oid,
            approver_name=KAVITA.name,
        ),
    ),
    refunds=(
        RefundRow(
            id=900301,
            order_id=STUCK_ON_ADDRESS,
            region="west",
            amount_minor=10000,
            status="approved",
            pending_action_id=_action(1),
        ),
        RefundRow(
            id=900302,
            order_id=STUCK_ON_ADDRESS,
            region="west",
            amount_minor=5000,
            status="failed",
            pending_action_id=_action(2),
            failure_reason="declined",
        ),
        RefundRow(
            id=900303,
            order_id=CHITRA_ORDER,
            region="west",
            amount_minor=30000,
            status="sent_to_payments",
            pending_action_id=_action(4),
            ticket_id=SENT_TO_PAYMENTS,
        ),
        RefundRow(
            id=900304,
            order_id=CHITRA_ORDER,
            region="west",
            amount_minor=20000,
            status="failed",
            pending_action_id=_action(9),
            ticket_id=PAYMENT_FAILED,
            failure_reason="Card closed",
        ),
        RefundRow(
            id=900305,
            order_id=CHITRA_ORDER,
            region="west",
            amount_minor=40000,
            status="paid",
            pending_action_id=_action(10),
            ticket_id=PAID,
        ),
    ),
)

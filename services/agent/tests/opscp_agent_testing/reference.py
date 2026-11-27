"""The reference case as rows: order 4471, stuck for four days, and ticket 2207 about it."""

from dataclasses import replace

from opscp_mcp_testing.identities import PRIYA
from opscp_mcp_testing.world import (
    CustomerRow,
    EventRow,
    NoteRow,
    OrderRow,
    TicketRow,
    World,
    ago,
)

ANANYA, BALA = 3001, 3002
ORDER, SOUTH_ORDER, FRESH_ORDER = 4471, 4720, 4490
TICKET, SOUTH_TICKET, FRESH_TICKET = 2207, 2240, 2290
ORDER_AMOUNT = 620_000
GOODWILL = 62_000

_TICKET = TicketRow(
    id=TICKET,
    order_id=ORDER,
    customer_id=ANANYA,
    region="west",
    status="open",
    priority="P2",
    summary="Order stuck for four days",
    updated_at=ago(8),
)

FIRST_HALF = World(
    customers=(
        CustomerRow(
            id=ANANYA,
            full_name="Ananya Kulkarni",
            email="ananya.k@example.in",
            phone="+91 58200 11471",
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
            id=ORDER,
            customer_id=ANANYA,
            status="packed",
            region="west",
            status_changed_at=ago(96),
            amount_minor=ORDER_AMOUNT,
        ),
        OrderRow(
            id=SOUTH_ORDER,
            customer_id=BALA,
            status="packed",
            region="south",
            status_changed_at=ago(60),
        ),
        OrderRow(
            id=FRESH_ORDER,
            customer_id=ANANYA,
            status="packed",
            region="west",
            status_changed_at=ago(5),
        ),
    ),
    events=(
        EventRow(order_id=ORDER, event_type="payment_released", occurred_at=ago(119)),
        EventRow(order_id=ORDER, event_type="packed", occurred_at=ago(96)),
        EventRow(
            order_id=ORDER,
            event_type="address_validation_failed",
            occurred_at=ago(23.5),
            payload={"reason": "postcode_mismatch", "attempt": 4},
        ),
        EventRow(order_id=SOUTH_ORDER, event_type="packed", occurred_at=ago(60)),
        EventRow(order_id=FRESH_ORDER, event_type="packed", occurred_at=ago(5)),
    ),
    tickets=(
        _TICKET,
        TicketRow(
            id=SOUTH_TICKET, order_id=SOUTH_ORDER, customer_id=BALA, region="south", status="open"
        ),
        TicketRow(
            id=FRESH_TICKET, order_id=FRESH_ORDER, customer_id=ANANYA, region="west", status="open"
        ),
    ),
    notes=(
        NoteRow(
            ticket_id=TICKET,
            author_kind="customer",
            author_name="Ananya Kulkarni",
            body="My order has not moved for days. Can someone check?",
            created_at=ago(60),
        ),
        NoteRow(
            ticket_id=TICKET,
            author_kind="customer",
            author_name="Ananya Kulkarni",
            body="This is the second time I am writing. It has been four days.",
            created_at=ago(8),
        ),
    ),
    pending_actions=(),
    refunds=(),
)

# As the first half leaves things: the ticket is in the L2 queue with Priya's recommendation.
SECOND_HALF = replace(
    FIRST_HALF,
    tickets=(
        replace(
            _TICKET,
            queue="L2",
            escalated_by_oid=PRIYA.oid,
            escalated_by_name=PRIYA.name,
            escalated_at=ago(1),
            recommendation={
                "amount_minor": GOODWILL,
                "reason_code": "goodwill_delay",
                "reason_text": "Delayed order: goodwill refund under the refund policy.",
            },
            version=2,
        ),
        *FIRST_HALF.tickets[1:],
    ),
)

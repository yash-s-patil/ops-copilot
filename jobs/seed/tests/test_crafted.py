from datetime import datetime, timedelta

import pytest

from opscp_common.hashing import args_hash
from opscp_common.limits import Limits
from opscp_seed.model import Dataset, Order, OrderEvent, Ticket, TicketNote

from .conftest import ARJUN, NOW

CASES = [
    (4471, 2207, "P2", "L1", "Ananya Kulkarni", "premium", "west", 620_000, "packed"),
    (4502, 2188, "P3", "L1", "Vikram Shah", "premium", "west", 380_000, "shipped"),
    (4533, 2214, "P3", "L1", "Meera Joshi", "standard", "west", 145_000, "paid"),
    (4610, 2231, "P1", "L2", "Sanjay Gupta", "standard", "west", 1_240_000, "lost"),
    (3912, 2199, "P2", "L2", "Deepa Reddy", "standard", "west", 890_000, "lost"),
    (4655, 2240, "P3", "L1", "Lakshmi Menon", "premium", "south", 290_000, "packed"),
    (4720, 2260, "P3", "L1", "Nikhil Desai", "standard", "west", 510_000, "shipped"),
    (4800, 2252, "P2", "L1", "Farhan Qureshi", "premium", "west", 775_000, "shipped"),
]


def _order(dataset: Dataset, order_id: int) -> Order:
    return next(order for order in dataset.orders if order.id == order_id)


def _ticket(dataset: Dataset, ticket_id: int) -> Ticket:
    return next(ticket for ticket in dataset.tickets if ticket.id == ticket_id)


def _events(dataset: Dataset, order_id: int) -> list[OrderEvent]:
    events = [event for event in dataset.events if event.order_id == order_id]
    return sorted(events, key=lambda event: event.occurred_at)


def _notes(dataset: Dataset, ticket_id: int) -> list[TicketNote]:
    return [note for note in dataset.notes if note.ticket_id == ticket_id]


def _hours_ago(hours: float) -> datetime:
    return NOW - timedelta(hours=hours)


@pytest.mark.parametrize(
    ("order_id", "ticket_id", "priority", "queue", "name", "tier", "region", "amount", "status"),
    CASES,
)
def test_crafted_case_matches_the_specification(  # noqa: PLR0913
    dataset: Dataset,
    order_id: int,
    ticket_id: int,
    priority: str,
    queue: str,
    name: str,
    tier: str,
    region: str,
    amount: int,
    status: str,
) -> None:
    order, ticket = _order(dataset, order_id), _ticket(dataset, ticket_id)
    customer = next(c for c in dataset.customers if c.id == order.customer_id)

    assert (order.amount_minor, order.status, order.region) == (amount, status, region)
    assert (customer.full_name, customer.tier, customer.region) == (name, tier, region)
    assert (ticket.order_id, ticket.priority, ticket.queue) == (order_id, priority, queue)
    assert (ticket.status, ticket.region, ticket.customer_id) == ("open", region, customer.id)


def test_reference_order_is_packed_for_96_hours_after_a_postcode_mismatch(
    dataset: Dataset,
) -> None:
    last_event = _events(dataset, 4471)[-1]

    assert _order(dataset, 4471).status_changed_at == _hours_ago(96)
    assert last_event.event_type == "address_validation_failed"
    assert last_event.payload["reason"] == "postcode_mismatch"


def test_reference_ticket_has_two_notes_from_the_customer(dataset: Dataset) -> None:
    notes = _notes(dataset, 2207)

    assert [(note.author_kind, note.author_name) for note in notes] == [
        ("customer", "Ananya Kulkarni"),
        ("customer", "Ananya Kulkarni"),
    ]


def test_order_4502_has_a_carrier_exception_80_hours_old_and_no_scan_since(
    dataset: Dataset,
) -> None:
    last_event = _events(dataset, 4502)[-1]

    assert (last_event.event_type, last_event.occurred_at) == ("carrier_exception", _hours_ago(80))


def test_order_4533_has_a_payment_hold_30_hours_old(dataset: Dataset) -> None:
    events = _events(dataset, 4533)

    assert [(e.event_type, e.occurred_at) for e in events] == [("payment_hold", _hours_ago(30))]


@pytest.mark.parametrize("order_id", [4610, 3912])
def test_lost_orders_have_the_carrier_confirmation_a_full_refund_needs(
    dataset: Dataset, order_id: int
) -> None:
    assert _events(dataset, order_id)[-1].event_type == "lost_in_transit_confirmed"


def test_south_order_is_packed_for_60_hours(dataset: Dataset) -> None:
    assert _order(dataset, 4655).status_changed_at == _hours_ago(60)


def test_order_4720_is_delayed_130_hours_and_one_note_carries_an_injected_instruction(
    dataset: Dataset,
) -> None:
    injected = [
        note for note in _notes(dataset, 2260) if "Ignore previous instructions" in note.body
    ]

    assert _events(dataset, 4720)[-1].occurred_at == _hours_ago(130)
    assert len(injected) == 1
    assert injected[0].author_kind == "customer"
    assert "issue_refund" in injected[0].body


def test_order_4800_has_2000_scans_from_one_hub(dataset: Dataset) -> None:
    scans = [event for event in _events(dataset, 4800) if event.event_type == "carrier_scan"]

    assert len(scans) == 2000
    assert all(scan.payload["hub"] == "PNQ-2" for scan in scans)
    assert len({scan.occurred_at for scan in scans}) == 2000


def test_a_full_refund_on_order_3912_is_waiting_for_a_supervisor(
    dataset: Dataset, limits: Limits
) -> None:
    (action,) = dataset.pending_actions

    assert (action.tool, action.approval_kind, action.ticket_id) == (
        "issue_refund",
        "supervisor",
        2199,
    )
    assert (action.requester_oid, action.requester_name) == (ARJUN.oid, "Arjun Mehta")
    assert action.args["order_id"] == 3912
    assert action.args["amount_minor"] == _order(dataset, 3912).amount_minor
    assert _order(dataset, 3912).amount_minor > limits.approval.self_limit_minor
    assert action.args_hash == args_hash("issue_refund", action.args)
    assert action.expires_at - action.created_at == timedelta(hours=24)
    assert action.created_at == NOW


def test_crafted_l2_tickets_were_not_escalated_by_a_demo_engineer(dataset: Dataset) -> None:
    for ticket_id in (2231, 2199):
        assert _ticket(dataset, ticket_id).escalated_by_oid is None

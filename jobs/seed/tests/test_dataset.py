import re
from collections import Counter
from datetime import timedelta

import pytest

from opscp_common.limits import Limits
from opscp_seed.dataset import build_dataset
from opscp_seed.load import FIRST_ID_AFTER_SEED
from opscp_seed.model import Dataset, Ticket

from .conftest import DEMO_USERS, NOW, PRIYA, ROHAN

CRAFTED_ORDER_IDS = {4471, 4502, 4533, 4610, 3912, 4655, 4720, 4800}
GENERATED_TICKET_IDS = range(1001, 1401)
PERCENT = 100
EMAIL = re.compile(r"[a-z]+\.[a-z]+\d*@example\.in")
PHONE = re.compile(r"\+91 5\d{4} \d{5}")


def _generated_tickets(dataset: Dataset) -> list[Ticket]:
    return [ticket for ticket in dataset.tickets if ticket.id in GENERATED_TICKET_IDS]


def test_same_inputs_give_the_same_dataset(dataset: Dataset, limits: Limits) -> None:
    assert build_dataset(now=NOW, limits=limits, demo=DEMO_USERS) == dataset


def test_a_later_run_moves_every_time_by_the_same_amount(dataset: Dataset, limits: Limits) -> None:
    later = build_dataset(now=NOW + timedelta(days=3), limits=limits, demo=DEMO_USERS)

    assert [order.placed_at - timedelta(days=3) for order in later.orders] == [
        order.placed_at for order in dataset.orders
    ]
    assert [order.status for order in later.orders] == [order.status for order in dataset.orders]


def test_customer_count_and_mix(dataset: Dataset) -> None:
    premium = sum(customer.tier == "premium" for customer in dataset.customers)
    west = sum(customer.region == "west" for customer in dataset.customers)

    assert len(dataset.customers) == 500
    assert 0.17 <= premium / 500 <= 0.23
    assert 0.66 <= west / 500 <= 0.74


def test_customer_ids_and_emails_are_unique_and_fictional(dataset: Dataset) -> None:
    emails = [customer.email for customer in dataset.customers]

    assert len({customer.id for customer in dataset.customers}) == 500
    assert len(set(emails)) == 500
    assert all(EMAIL.fullmatch(email) for email in emails)


def test_phone_numbers_are_outside_the_indian_mobile_ranges(dataset: Dataset) -> None:
    assert all(PHONE.fullmatch(customer.phone) for customer in dataset.customers)


def test_seeded_ids_stay_below_the_first_id_given_to_new_rows(dataset: Dataset) -> None:
    seeded = (
        [customer.id for customer in dataset.customers]
        + [order.id for order in dataset.orders]
        + [ticket.id for ticket in dataset.tickets]
    )

    assert max(seeded) < FIRST_ID_AFTER_SEED
    assert len(dataset.notes) < FIRST_ID_AFTER_SEED


def test_a_ticket_about_a_finished_order_is_raised_after_the_order_reached_that_state(
    dataset: Dataset,
) -> None:
    orders = {order.id: order for order in dataset.orders}

    for ticket in dataset.tickets:
        order = orders[ticket.order_id]
        if order.status in ("lost", "delivered"):
            assert ticket.created_at >= order.status_changed_at


def test_generated_orders_fill_the_id_range_around_the_crafted_order(dataset: Dataset) -> None:
    generated = [order.id for order in dataset.orders if order.id not in CRAFTED_ORDER_IDS]

    assert generated == [order_id for order_id in range(1001, 4001) if order_id != 3912]
    assert {order.id for order in dataset.orders} - set(generated) == CRAFTED_ORDER_IDS


def test_event_volume(dataset: Dataset) -> None:
    per_order = Counter(event.order_id for event in dataset.events)
    generated = sum(count for order_id, count in per_order.items() if order_id != 4800)

    assert 8_500 <= generated <= 9_900


def test_ticket_count_and_mix(dataset: Dataset) -> None:
    generated = _generated_tickets(dataset)
    still_open = [ticket for ticket in generated if ticket.status == "open"]
    with_l2 = sum(ticket.queue == "L2" for ticket in still_open)

    assert [ticket.id for ticket in generated] == list(GENERATED_TICKET_IDS)
    assert len(dataset.tickets) == 408
    assert 0.55 <= len(still_open) / 400 <= 0.65
    assert 0.15 <= with_l2 / len(still_open) <= 0.25


def test_note_volume(dataset: Dataset) -> None:
    assert 750 <= len(dataset.notes) <= 900


def test_every_ticket_has_one_order_and_at_most_one_ticket_per_order(dataset: Dataset) -> None:
    order_ids = [ticket.order_id for ticket in dataset.tickets]

    assert len(set(order_ids)) == len(order_ids)


def test_regions_and_customers_agree_across_related_rows(dataset: Dataset) -> None:
    customers = {customer.id: customer for customer in dataset.customers}
    orders = {order.id: order for order in dataset.orders}

    assert all(order.region == customers[order.customer_id].region for order in dataset.orders)
    for ticket in dataset.tickets:
        order = orders[ticket.order_id]
        assert (ticket.region, ticket.customer_id) == (order.region, order.customer_id)


def test_times_are_ordered_and_never_in_the_future(dataset: Dataset) -> None:
    customers = {customer.id: customer for customer in dataset.customers}
    orders = {order.id: order for order in dataset.orders}
    tickets = {ticket.id: ticket for ticket in dataset.tickets}

    for order in dataset.orders:
        assert customers[order.customer_id].created_at < order.placed_at
        assert order.placed_at <= order.status_changed_at <= NOW
    for event in dataset.events:
        assert orders[event.order_id].placed_at <= event.occurred_at <= NOW
    for ticket in dataset.tickets:
        assert orders[ticket.order_id].placed_at < ticket.created_at <= ticket.updated_at <= NOW
    for note in dataset.notes:
        ticket = tickets[note.ticket_id]
        assert ticket.created_at <= note.created_at <= ticket.updated_at


def test_a_carrier_and_tracking_reference_exist_once_an_order_has_shipped(
    dataset: Dataset,
) -> None:
    picked_up = {e.order_id for e in dataset.events if e.event_type == "carrier_pickup"}

    for order in dataset.orders:
        shipped = order.id in picked_up
        assert (order.carrier is not None) == shipped
        assert (order.tracking_ref is not None) == shipped


def test_the_first_note_on_every_ticket_is_from_its_customer(dataset: Dataset) -> None:
    customers = {customer.id: customer for customer in dataset.customers}
    first_notes: dict[int, tuple[str, str]] = {}
    for note in dataset.notes:
        first_notes.setdefault(note.ticket_id, (note.author_kind, note.author_name))

    for ticket in dataset.tickets:
        assert first_notes[ticket.id] == ("customer", customers[ticket.customer_id].full_name)


def test_engineer_notes_carry_the_author_and_customer_notes_do_not(dataset: Dataset) -> None:
    for note in dataset.notes:
        assert (note.author_oid is not None) == (note.author_kind == "engineer")


def test_generated_l2_tickets_record_who_escalated_them(dataset: Dataset) -> None:
    for ticket in _generated_tickets(dataset):
        escalated = ticket.queue == "L2"
        assert (ticket.escalated_by_oid is not None) == escalated
        assert (ticket.escalated_by_name is not None) == escalated
        assert (ticket.escalated_at is not None) == escalated


def test_demo_engineers_have_escalated_tickets_only_in_their_own_region(
    dataset: Dataset,
) -> None:
    regions = {
        engineer.name: {t.region for t in dataset.tickets if t.escalated_by_oid == engineer.oid}
        for engineer in (PRIYA, ROHAN)
    }

    assert regions == {"Priya Nair": {"west"}, "Rohan Iyer": {"south"}}


def test_ongoing_problems_are_never_marked_resolved(dataset: Dataset) -> None:
    orders = {order.id: order for order in dataset.orders}
    in_progress = {"paid", "packed", "shipped"}

    for ticket in dataset.tickets:
        if orders[ticket.order_id].status in in_progress:
            assert ticket.status in ("open", "pending_customer")


def test_recommendations_are_within_the_refund_policy(dataset: Dataset, limits: Limits) -> None:
    customers = {customer.id: customer for customer in dataset.customers}
    orders = {order.id: order for order in dataset.orders}
    recommended = [ticket for ticket in dataset.tickets if ticket.recommendation is not None]

    assert recommended
    for ticket in recommended:
        recommendation, order = ticket.recommendation, orders[ticket.order_id]
        assert recommendation is not None
        assert ticket.queue == "L2"
        amount = recommendation["amount_minor"]
        assert isinstance(amount, int)
        assert 0 < amount <= order.amount_minor
        if recommendation["reason_code"] == "goodwill_delay":
            premium = customers[ticket.customer_id].tier == "premium"
            rule = limits.goodwill.premium if premium else limits.goodwill.standard
            assert amount * PERCENT <= order.amount_minor * rule.max_percent


def test_text_fits_the_limits_the_tools_enforce(dataset: Dataset, limits: Limits) -> None:
    assert all(len(ticket.summary) <= limits.ticket.summary_chars for ticket in dataset.tickets)
    assert all(len(note.body) <= limits.ticket.note_write_chars for note in dataset.notes)


@pytest.mark.parametrize(
    "status", ["placed", "paid", "packed", "shipped", "delivered", "cancelled", "lost"]
)
def test_every_order_status_appears(dataset: Dataset, status: str) -> None:
    assert any(order.status == status for order in dataset.orders)

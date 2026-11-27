"""The simulated provider's memory: one result per idempotency key."""

import pytest

from opscp_payments.sim.store import KeyReusedError, Order, Store, new_reference

ORDER = Order(order_id=4471, amount_minor=62000, currency="INR")
DELAY_S = 3


class _Clock:
    def __init__(self) -> None:
        self.seconds = 100.0

    def __call__(self) -> float:
        return self.seconds


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def store(clock: _Clock) -> Store:
    return _store(clock, capacity=10_000)


def _store(clock: _Clock, *, capacity: int) -> Store:
    refs = iter(f"sim_{n}" for n in range(1, 100_000))
    return Store(settle_delay_s=DELAY_S, capacity=capacity, clock=clock, new_ref=lambda: next(refs))


def test_a_refund_is_processing_for_the_delay_and_then_succeeds(
    store: Store, clock: _Clock
) -> None:
    refund = store.accept("refund-1", ORDER, decline=False)
    at_once = store.status(refund)
    clock.seconds += DELAY_S - 0.01
    just_before = store.status(refund)
    clock.seconds += 0.01

    assert (at_once, just_before, store.status(refund)) == ("processing", "processing", "succeeded")
    assert store.find(refund.provider_ref) == refund


def test_a_repeated_key_gets_the_first_refund_and_no_second_one(
    store: Store, clock: _Clock
) -> None:
    first = store.accept("refund-1", ORDER, decline=False)
    clock.seconds += 10

    again = store.accept("refund-1", ORDER, decline=True)

    assert again is first
    assert store.status(again) == "succeeded"
    assert store.find("sim_2") is None


def test_a_key_that_comes_back_for_a_different_order_is_a_mistake(store: Store) -> None:
    store.accept("refund-1", ORDER, decline=False)

    for other in (
        Order(4472, 62000, "INR"),
        Order(4471, 62001, "INR"),
        Order(4471, 62000, "USD"),
    ):
        with pytest.raises(KeyReusedError):
            store.accept("refund-1", other, decline=False)


def test_a_refund_marked_to_decline_is_declined_after_the_delay(
    store: Store, clock: _Clock
) -> None:
    refund = store.accept("refund-1", ORDER, decline=True)
    before = store.status(refund)
    clock.seconds += DELAY_S

    assert (before, store.status(refund)) == ("processing", "declined")


def test_a_declined_refund_moved_no_money_so_its_key_may_ask_again(
    store: Store, clock: _Clock
) -> None:
    declined = store.accept("refund-1", ORDER, decline=True)
    clock.seconds += DELAY_S

    retried = store.accept("refund-1", ORDER, decline=False)
    clock.seconds += DELAY_S

    assert retried.provider_ref != declined.provider_ref
    assert (store.status(declined), store.status(retried)) == ("declined", "succeeded")
    assert store.accept("refund-1", ORDER, decline=False) is retried


def test_a_declining_refund_still_processing_is_not_replaced(store: Store) -> None:
    first = store.accept("refund-1", ORDER, decline=True)

    assert store.accept("refund-1", ORDER, decline=False) is first


def test_an_unknown_reference_is_not_found(store: Store) -> None:
    assert store.find("sim_none") is None


def test_the_oldest_refunds_are_forgotten_so_memory_stays_bounded(clock: _Clock) -> None:
    store = _store(clock, capacity=3)
    first = store.accept("refund-1", ORDER, decline=True)
    for number in range(2, 5):
        store.accept(f"refund-{number}", ORDER, decline=False)

    assert store.find(first.provider_ref) is None
    assert store.find("sim_4") is not None
    assert store.accept("refund-1", ORDER, decline=False).provider_ref == "sim_5"


def test_references_of_refunds_declined_and_replaced_do_not_pile_up(clock: _Clock) -> None:
    store = _store(clock, capacity=2)
    for _ in range(6):
        store.accept("refund-1", ORDER, decline=True)
        clock.seconds += DELAY_S

    assert store.find("sim_1") is None
    assert store.find("sim_6") is not None


def test_a_reference_is_opaque_and_new_each_time() -> None:
    first, second = new_reference(), new_reference()

    assert first != second
    assert first.startswith("sim_")
    assert len(first) == len("sim_") + 32

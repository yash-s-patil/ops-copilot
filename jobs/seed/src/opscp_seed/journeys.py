"""How an order moves from placed to its current state, one event at a time."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from random import Random

from opscp_common.hashing import JsonValue
from opscp_common.vocabulary import EventType, OrderStatus, Region
from opscp_seed import issues
from opscp_seed.issues import Issue
from opscp_seed.model import Customer, Json, Order, OrderEvent

SECONDS_PER_HOUR = 3600
HOURS_PER_DAY = 24

CARRIERS: dict[str, str] = {
    "BlueArrow Logistics": "BA",
    "Sahyadri Express": "SX",
    "Deccan Couriers": "DC",
    "Konkan Cargo": "KC",
}
HUBS: dict[Region, tuple[str, ...]] = {
    "west": ("BOM-1", "PNQ-2", "AMD-1", "NAG-1"),
    "south": ("BLR-1", "MAA-2", "HYD-1", "COK-1"),
}
WAREHOUSES: dict[Region, tuple[str, ...]] = {
    "west": ("WH-BOM", "WH-PNQ"),
    "south": ("WH-BLR", "WH-MAA"),
}
EXCEPTION_REASONS = ("hub_misroute", "address_not_found", "vehicle_breakdown", "weather_delay")
DAMAGE_DETAILS = ("box_crushed", "item_broken", "water_damage")
RECEIVERS = ("customer", "family_member", "security_desk", "neighbour")
CANCEL_REASONS = ("customer_request", "out_of_stock", "payment_failed")
SCAN_COUNTS = (0, 1, 2)
SCAN_COUNT_WEIGHTS = (70, 25, 5)

PRICE_LOG_MEAN = 7.3
PRICE_LOG_SPREAD = 0.9
PRICE_MIN_RUPEES = 199
PRICE_MAX_RUPEES = 49_999
PRICE_STEP_RUPEES = 100
PRICE_ENDING_RUPEES = 99
MINOR_UNITS_PER_RUPEE = 100
TRACKING_DIGITS = 10


def hours(amount: float) -> timedelta:
    return timedelta(seconds=round(amount * SECONDS_PER_HOUR))


class Journey:
    """An order's life so far, measured in hours after it was placed."""

    def __init__(self, rng: Random, region: Region) -> None:
        self._rng = rng
        self._region: Region = region
        self.elapsed = 0.0
        self.status: OrderStatus = "placed"
        self.status_since = 0.0
        self.problem_since: float | None = None
        self.carrier: str | None = None
        self.events: list[tuple[float, EventType, Json]] = []

    def _wait(self, low: float, high: float) -> None:
        self.elapsed += self._rng.uniform(low, high)

    def _record(self, event_type: EventType, **payload: JsonValue) -> None:
        self.events.append((self.elapsed, event_type, payload))

    def _enter(self, status: OrderStatus) -> None:
        self.status = status
        self.status_since = self.elapsed

    def flag_problem(self) -> None:
        self.problem_since = self.elapsed

    def pay(self) -> None:
        self._wait(0.05, 0.5)
        self._enter("paid")

    def hold_payment(self) -> None:
        self._record("payment_hold", reason="risk_review")

    def release_payment(self) -> None:
        self._wait(4, 30)
        self._record("payment_released")

    def pack(self) -> None:
        self._wait(3, 30)
        self._enter("packed")
        self._record("packed", warehouse=self._rng.choice(WAREHOUSES[self._region]))

    def fail_address_validation(self) -> None:
        self._wait(0.2, 2)
        self._record("address_validation_failed", reason="postcode_mismatch")

    def ship(self, scans: int | None = None) -> None:
        self._wait(4, 26)
        self.carrier = self._rng.choice(tuple(CARRIERS))
        self._enter("shipped")
        self._record("carrier_pickup", carrier=self.carrier, hub=self._hub())
        if scans is None:
            scans = self._rng.choices(SCAN_COUNTS, SCAN_COUNT_WEIGHTS)[0]
        for _ in range(scans):
            self.scan()

    def scan(self) -> None:
        self._wait(6, 30)
        self._record(
            "carrier_scan", hub=self._hub(), scan=self._rng.choice(("arrived", "departed"))
        )

    def raise_exception(self) -> None:
        self._wait(4, 20)
        self._record("carrier_exception", reason=self._rng.choice(EXCEPTION_REASONS))
        self.flag_problem()

    def lose(self) -> None:
        self._wait(48, 120)
        self._enter("lost")
        self._record("lost_in_transit_confirmed", confirmed_by=self.carrier)

    def deliver(self) -> None:
        self._wait(8, 30)
        self._enter("delivered")
        self._record("delivered", received_by=self._rng.choice(RECEIVERS))

    def report_damage(self) -> None:
        self._wait(2, 48)
        self._record("damage_reported", detail=self._rng.choice(DAMAGE_DETAILS))
        self.flag_problem()

    def cancel(self) -> None:
        self._wait(0.5, 20)
        self._enter("cancelled")
        self._record("cancelled", reason=self._rng.choice(CANCEL_REASONS))

    def _hub(self) -> str:
        return self._rng.choice(HUBS[self._region])


def _placed(journey: Journey) -> None:
    """Nothing has happened to the order yet."""


def _paid(journey: Journey) -> None:
    journey.pay()


def _packed(journey: Journey) -> None:
    journey.pay()
    journey.pack()


def _in_transit(journey: Journey) -> None:
    _packed(journey)
    journey.ship()


def _delivered(journey: Journey) -> None:
    _in_transit(journey)
    journey.deliver()


def _delivered_after_hold(journey: Journey) -> None:
    journey.pay()
    journey.hold_payment()
    journey.release_payment()
    journey.pack()
    journey.ship()
    journey.deliver()


def _cancelled(journey: Journey) -> None:
    journey.pay()
    journey.cancel()


def _stuck_packed(journey: Journey) -> None:
    _packed(journey)
    journey.flag_problem()


def _stuck_on_address(journey: Journey) -> None:
    _stuck_packed(journey)
    journey.fail_address_validation()


def _payment_hold(journey: Journey) -> None:
    journey.pay()
    journey.hold_payment()
    journey.flag_problem()


def _carrier_exception(journey: Journey) -> None:
    _packed(journey)
    journey.ship(scans=1)
    journey.raise_exception()


def _tracking_stale(journey: Journey) -> None:
    _packed(journey)
    journey.ship(scans=1)
    journey.flag_problem()


def _lost(journey: Journey) -> None:
    _carrier_exception(journey)
    journey.lose()
    journey.flag_problem()


def _damaged(journey: Journey) -> None:
    _delivered(journey)
    journey.report_damage()


def _delivered_late(journey: Journey) -> None:
    _carrier_exception(journey)
    journey.scan()
    journey.deliver()
    journey.flag_problem()


def _not_received(journey: Journey) -> None:
    _delivered(journey)
    journey.flag_problem()


@dataclass(frozen=True, slots=True)
class _Path:
    weight: float
    build: Callable[[Journey], None]
    quiet_hours: tuple[float, float]
    issue: Issue | None = None


_PATHS: tuple[_Path, ...] = (
    _Path(62.0, _delivered, (2, 85 * HOURS_PER_DAY)),
    _Path(5.0, _delivered_after_hold, (2, 85 * HOURS_PER_DAY)),
    _Path(9.0, _cancelled, (1, 85 * HOURS_PER_DAY)),
    _Path(4.0, _in_transit, (1, 30)),
    _Path(2.0, _packed, (1, 30)),
    _Path(1.5, _paid, (0.2, 10)),
    _Path(0.5, _placed, (0, 0.5)),
    _Path(1.6, _stuck_packed, (50, 150), issues.STUCK_PACKED),
    _Path(1.0, _stuck_on_address, (50, 150), issues.STUCK_PACKED),
    _Path(1.6, _payment_hold, (26, 80), issues.PAYMENT_HOLD),
    _Path(2.6, _carrier_exception, (20, 130), issues.CARRIER_EXCEPTION),
    _Path(2.4, _tracking_stale, (75, 170), issues.TRACKING_STALE),
    _Path(1.6, _lost, (5, 60 * HOURS_PER_DAY), issues.LOST),
    _Path(1.6, _damaged, (3, 60 * HOURS_PER_DAY), issues.DAMAGED),
    _Path(2.2, _delivered_late, (24, 80 * HOURS_PER_DAY), issues.DELIVERED_LATE),
    _Path(1.4, _not_received, (3, 20 * HOURS_PER_DAY), issues.NOT_RECEIVED),
)


@dataclass(frozen=True, slots=True)
class Problem:
    issue: Issue
    since: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderCase:
    order: Order
    customer: Customer
    events: tuple[OrderEvent, ...]
    problem: Problem | None


def _amount_minor(rng: Random) -> int:
    rupees = round(rng.lognormvariate(PRICE_LOG_MEAN, PRICE_LOG_SPREAD))
    rupees = min(max(rupees, PRICE_MIN_RUPEES), PRICE_MAX_RUPEES)
    rupees = rupees // PRICE_STEP_RUPEES * PRICE_STEP_RUPEES + PRICE_ENDING_RUPEES
    return rupees * MINOR_UNITS_PER_RUPEE


def _tracking_ref(rng: Random, carrier: str) -> str:
    return CARRIERS[carrier] + "".join(rng.choices("0123456789", k=TRACKING_DIGITS))


def order_case(rng: Random, now: datetime, order_id: int, customer: Customer) -> OrderCase:
    path = rng.choices(_PATHS, [candidate.weight for candidate in _PATHS])[0]
    journey = Journey(rng, customer.region)
    path.build(journey)
    placed_at = now - hours(journey.elapsed + rng.uniform(*path.quiet_hours))
    order = Order(
        id=order_id,
        customer_id=customer.id,
        status=journey.status,
        amount_minor=_amount_minor(rng),
        region=customer.region,
        carrier=journey.carrier,
        tracking_ref=_tracking_ref(rng, journey.carrier) if journey.carrier else None,
        placed_at=placed_at,
        status_changed_at=placed_at + hours(journey.status_since),
    )
    events = tuple(
        OrderEvent(
            order_id=order_id,
            event_type=event_type,
            payload=payload,
            occurred_at=placed_at + hours(after),
        )
        for after, event_type, payload in journey.events
    )
    problem = None
    if path.issue is not None and journey.problem_since is not None:
        problem = Problem(path.issue, placed_at + hours(journey.problem_since))
    return OrderCase(order=order, customer=customer, events=events, problem=problem)

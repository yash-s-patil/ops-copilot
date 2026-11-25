"""Builds the whole demo dataset: generated customers, orders and tickets plus the crafted cases."""

from datetime import datetime, timedelta
from random import Random
from string import ascii_lowercase
from uuid import UUID

from faker import Faker

from opscp_common.limits import Limits
from opscp_seed import crafted
from opscp_seed.journeys import HOURS_PER_DAY, OrderCase, hours, order_case
from opscp_seed.model import Customer, Dataset, Engineer, Region, Tier
from opscp_seed.settings import DemoUsers
from opscp_seed.tickets import Staff, TicketWriter

SEED = 4471
LOCALE = "en_IN"
EMAIL_DOMAIN = "example.in"

CUSTOMER_COUNT = 500
PREMIUM_SHARE = 0.2
WEST_SHARE = 0.7
CUSTOMER_AGE_DAYS = (100, 1100)
ORDER_IDS = range(1001, 4001)
TICKET_IDS = range(1001, 1401)
UUID_BITS = 128
PHONE_PREFIX_RANGE = (50000, 59999)
PHONE_SUFFIX_RANGE = (10000, 99999)

_INVENTED_L1: dict[Region, tuple[str, ...]] = {
    "west": ("Neha Pillai", "Imran Sheikh", "Tanvi Bhosale"),
    "south": ("Divya Krishnan", "Karthik Subramanian"),
}
_INVENTED_L2: dict[Region, tuple[str, ...]] = {
    "west": ("Rahul Deshpande",),
    "south": ("Anil Varghese", "Shalini Rajan"),
}


def _engineers(rng: Random, names: tuple[str, ...]) -> tuple[Engineer, ...]:
    return tuple(
        Engineer(oid=UUID(int=rng.getrandbits(UUID_BITS), version=4), name=name) for name in names
    )


def _staff(rng: Random, demo: DemoUsers) -> Staff:
    return Staff(
        l1={
            "west": (demo.l1_west, *_engineers(rng, _INVENTED_L1["west"])),
            "south": (demo.l1_south, *_engineers(rng, _INVENTED_L1["south"])),
        },
        l2={
            "west": (demo.l2_west, *_engineers(rng, _INVENTED_L2["west"])),
            "south": _engineers(rng, _INVENTED_L2["south"]),
        },
    )


def _letters(name: str) -> str:
    return "".join(character for character in name.lower() if character in ascii_lowercase)


def _customers(rng: Random, faker: Faker, now: datetime) -> list[Customer]:
    taken = set(crafted.EMAILS)
    first_id = crafted.FIRST_CUSTOMER_ID + crafted.CUSTOMER_COUNT
    customers: list[Customer] = []
    for customer_id in range(first_id, crafted.FIRST_CUSTOMER_ID + CUSTOMER_COUNT):
        first, last = faker.first_name(), faker.last_name()
        mailbox = f"{_letters(first)}.{_letters(last)}"
        email = f"{mailbox}@{EMAIL_DOMAIN}"
        if email in taken:
            email = f"{mailbox}{customer_id}@{EMAIL_DOMAIN}"
        taken.add(email)
        tier: Tier = "premium" if rng.random() < PREMIUM_SHARE else "standard"
        region: Region = "west" if rng.random() < WEST_SHARE else "south"
        phone = f"+91 {rng.randint(*PHONE_PREFIX_RANGE)} {rng.randint(*PHONE_SUFFIX_RANGE)}"
        customers.append(
            Customer(
                id=customer_id,
                full_name=f"{first} {last}",
                email=email,
                phone=phone,
                tier=tier,
                region=region,
                created_at=now - hours(rng.uniform(*CUSTOMER_AGE_DAYS) * HOURS_PER_DAY),
            )
        )
    return customers


def _generated(rng: Random, now: datetime, limits: Limits, demo: DemoUsers) -> Dataset:
    faker = Faker(LOCALE)
    faker.seed_instance(SEED)
    customers = _customers(rng, faker, now)
    cases: list[OrderCase] = [
        order_case(rng, now, order_id, rng.choice(customers))
        for order_id in ORDER_IDS
        if order_id not in crafted.ORDER_IDS
    ]
    with_problem = [(case, case.problem) for case in cases if case.problem is not None]
    writer = TicketWriter(rng, now, limits.goodwill, _staff(rng, demo))
    written = [
        writer.write(ticket_id, case, problem)
        for ticket_id, (case, problem) in zip(
            TICKET_IDS, rng.sample(with_problem, len(TICKET_IDS)), strict=True
        )
    ]
    return Dataset(
        customers=tuple(customers),
        orders=tuple(case.order for case in cases),
        events=tuple(event for case in cases for event in case.events),
        tickets=tuple(ticket for ticket, _ in written),
        notes=tuple(note for _, notes in written for note in notes),
        pending_actions=(),
    )


def build_dataset(*, now: datetime, limits: Limits, demo: DemoUsers) -> Dataset:
    """The same `now`, limits and demo users always give the same dataset."""
    now = now.replace(microsecond=0)
    generated = _generated(Random(SEED), now, limits, demo)
    by_hand = crafted.crafted_cases(
        now, demo.l2_west, timedelta(seconds=limits.approval.pending_ttl_supervisor_s)
    )
    return Dataset(
        customers=by_hand.customers + generated.customers,
        orders=generated.orders + by_hand.orders,
        events=generated.events + by_hand.events,
        tickets=generated.tickets + by_hand.tickets,
        notes=generated.notes + by_hand.notes,
        pending_actions=by_hand.pending_actions,
    )

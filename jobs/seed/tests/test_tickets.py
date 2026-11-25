from datetime import timedelta

import pytest

from opscp_common.limits import Limits
from opscp_seed import issues
from opscp_seed.issues import Issue
from opscp_seed.journeys import OrderCase, Problem
from opscp_seed.model import Customer, Order, Tier
from opscp_seed.tickets import recommend_refund

from .conftest import NOW

AMOUNT_MINOR = 400_000


def _case(tier: Tier) -> OrderCase:
    customer = Customer(
        id=1,
        full_name="Test Customer",
        email="test.customer@example.in",
        phone="+91 50000 00000",
        tier=tier,
        region="west",
        created_at=NOW - timedelta(days=200),
    )
    order = Order(
        id=1,
        customer_id=customer.id,
        status="packed",
        amount_minor=AMOUNT_MINOR,
        region="west",
        carrier=None,
        tracking_ref=None,
        placed_at=NOW - timedelta(days=9),
        status_changed_at=NOW - timedelta(days=8),
    )
    return OrderCase(order=order, customer=customer, events=(), problem=None)


def _problem(issue: Issue, hours_ago: float) -> Problem:
    return Problem(issue, NOW - timedelta(hours=hours_ago))


@pytest.mark.parametrize(("tier", "hours_ago"), [("premium", 72), ("standard", 120)])
def test_goodwill_is_recommended_from_the_minimum_delay_for_the_tier(
    limits: Limits, tier: Tier, hours_ago: float
) -> None:
    recommendation = recommend_refund(
        _case(tier), _problem(issues.STUCK_PACKED, hours_ago), NOW, limits.goodwill
    )

    assert recommendation == {
        "amount_minor": 40_000,
        "reason_code": "goodwill_delay",
        "reason_text": f"Delayed {hours_ago} hours",
    }


@pytest.mark.parametrize(("tier", "hours_ago"), [("premium", 71.9), ("standard", 119.9)])
def test_goodwill_is_not_recommended_before_the_minimum_delay(
    limits: Limits, tier: Tier, hours_ago: float
) -> None:
    problem = _problem(issues.CARRIER_EXCEPTION, hours_ago)

    assert recommend_refund(_case(tier), problem, NOW, limits.goodwill) is None


@pytest.mark.parametrize(
    ("issue", "reason"), [(issues.LOST, "lost_in_transit"), (issues.DAMAGED, "damaged")]
)
def test_loss_and_damage_recommend_the_full_amount(
    limits: Limits, issue: Issue, reason: str
) -> None:
    recommendation = recommend_refund(_case("standard"), _problem(issue, 1), NOW, limits.goodwill)

    assert recommendation is not None
    assert (recommendation["amount_minor"], recommendation["reason_code"]) == (AMOUNT_MINOR, reason)


@pytest.mark.parametrize("issue", [issues.PAYMENT_HOLD, issues.DELIVERED_LATE, issues.NOT_RECEIVED])
def test_no_refund_is_recommended_when_the_policy_has_no_reason_for_one(
    limits: Limits, issue: Issue
) -> None:
    assert recommend_refund(_case("premium"), _problem(issue, 500), NOW, limits.goodwill) is None

"""Every rule of the refund check, on facts stated in the test."""

from datetime import timedelta
from typing import Any

import pytest

from opscp_common.errors import PolicyViolationError
from opscp_common.limits import Limits
from opscp_common.vocabulary import OrderStatus, RefundReason
from opscp_mcp.ports.actions import RefundCase
from opscp_mcp.services.refund_policy import approval_kind, assess, delay_hours
from opscp_mcp_testing.world import NOW, ago

ORDER_VALUE = 620000


def _case(**changed: Any) -> RefundCase:
    """A premium customer's order, packed 100 hours ago, with nothing refunded."""
    facts: dict[str, Any] = {
        "order_id": 4471,
        "region": "west",
        "tier": "premium",
        "status": "packed",
        "status_changed_at": ago(100),
        "amount_minor": ORDER_VALUE,
        "refunded_minor": 0,
        "last_new_scan_at": None,
        "carrier_exception_at": None,
        "loss_confirmed": False,
        "damage_reported": False,
        "goodwill_given": False,
        "request_waiting": False,
    }
    return RefundCase.model_validate(facts | changed)


def _refusal(amount: int, reason: RefundReason, case: RefundCase, limits: Limits) -> dict[str, Any]:
    with pytest.raises(PolicyViolationError) as refused:
        assess(amount, reason, case, now=NOW, goodwill=limits.goodwill)
    return refused.value.body().details


def _allowed(amount: int, reason: RefundReason, case: RefundCase, limits: Limits) -> dict[str, Any]:
    return assess(amount, reason, case, now=NOW, goodwill=limits.goodwill)


@pytest.mark.parametrize(
    ("changed", "hours"),
    [
        ({"status": "paid", "status_changed_at": ago(30)}, 30),
        ({"status": "packed", "status_changed_at": ago(96.9)}, 96),
        ({"status": "shipped", "status_changed_at": ago(80)}, 80),
        ({"status": "shipped", "status_changed_at": ago(80), "last_new_scan_at": ago(10)}, 10),
        (
            {
                "status": "shipped",
                "status_changed_at": ago(80),
                "last_new_scan_at": ago(10),
                "carrier_exception_at": ago(50),
            },
            50,
        ),
        ({"status": "packed", "status_changed_at": NOW + timedelta(hours=2)}, 0),
        ({"status": "placed"}, None),
        ({"status": "delivered"}, None),
        ({"status": "cancelled"}, None),
        ({"status": "lost"}, None),
    ],
    ids=[
        "paid: since payment",
        "packed: whole hours since packing",
        "shipped and never scanned: since pickup",
        "shipped: since the last new scan",
        "shipped with an exception: since the exception",
        "a clock that disagrees gives no negative delay",
        "not paid for",
        "delivered",
        "cancelled",
        "lost",
    ],
)
def test_delay_is_measured_as_the_shipping_policy_says(
    changed: dict[str, Any], hours: int | None
) -> None:
    assert delay_hours(_case(**changed), NOW) == hours


def test_a_goodwill_refund_within_the_rules_is_allowed_and_what_was_seen_is_recorded(
    limits: Limits,
) -> None:
    assert _allowed(93000, "goodwill_delay", _case(), limits) == {
        "order_amount_minor": ORDER_VALUE,
        "refunded_minor": 0,
        "tier": "premium",
        "order_status": "packed",
        "delay_hours": 100,
        "cap_minor": 93000,
    }


@pytest.mark.parametrize(
    ("tier", "hours", "rule"),
    [
        ("premium", 71.9, "goodwill_delay_too_short"),
        ("premium", 72, None),
        ("standard", 119.9, "goodwill_delay_too_short"),
        ("standard", 120, None),
    ],
)
def test_goodwill_needs_72_hours_for_premium_and_120_for_standard(
    limits: Limits, tier: str, hours: float, rule: str | None
) -> None:
    case = _case(tier=tier, status_changed_at=ago(hours))

    if rule is None:
        assert _allowed(1000, "goodwill_delay", case, limits)["delay_hours"] == int(hours)
        return
    details = _refusal(1000, "goodwill_delay", case, limits)
    assert details == {
        "rule": rule,
        "cap_minor": 0,
        "requested_minor": 1000,
        "delay_hours": int(hours),
        "min_delay_hours": 72 if tier == "premium" else 120,
    }


@pytest.mark.parametrize(
    ("tier", "cap"), [("premium", 93000), ("standard", 62000)], ids=["15%", "10%"]
)
def test_goodwill_is_capped_at_a_share_of_the_order_value(
    limits: Limits, tier: str, cap: int
) -> None:
    case = _case(tier=tier, status_changed_at=ago(200))

    assert _allowed(cap, "goodwill_delay", case, limits)["cap_minor"] == cap
    assert _refusal(cap + 1, "goodwill_delay", case, limits) == {
        "rule": "goodwill_exceeds_cap",
        "cap_minor": cap,
        "requested_minor": cap + 1,
    }


def test_the_goodwill_cap_rounds_down_to_a_whole_paisa(limits: Limits) -> None:
    case = _case(amount_minor=999)

    assert _allowed(149, "goodwill_delay", case, limits)["cap_minor"] == 149
    assert _refusal(150, "goodwill_delay", case, limits)["cap_minor"] == 149


def test_the_goodwill_cap_is_no_more_than_what_is_left_to_refund(limits: Limits) -> None:
    case = _case(refunded_minor=ORDER_VALUE - 5000)

    assert _refusal(5001, "goodwill_delay", case, limits)["cap_minor"] == 5000
    assert _allowed(5000, "goodwill_delay", case, limits)["cap_minor"] == 5000


def test_only_one_goodwill_refund_is_made_on_an_order(limits: Limits) -> None:
    assert _refusal(1000, "goodwill_delay", _case(goodwill_given=True), limits) == {
        "rule": "goodwill_already_given",
        "cap_minor": 0,
        "requested_minor": 1000,
    }


@pytest.mark.parametrize("status", ["placed", "delivered", "cancelled", "lost"])
def test_goodwill_is_only_for_an_order_that_is_delayed_and_not_delivered(
    limits: Limits, status: OrderStatus
) -> None:
    case = _case(status=status, status_changed_at=ago(500))

    assert _refusal(1000, "goodwill_delay", case, limits)["rule"] == "goodwill_not_delayed"


@pytest.mark.parametrize(
    ("reason", "evidence", "rule"),
    [
        ("lost_in_transit", {"loss_confirmed": True, "status": "lost"}, "loss_not_confirmed"),
        ("cancelled", {"status": "cancelled"}, "not_cancelled"),
        ("damaged", {"damage_reported": True, "status": "delivered"}, "damage_not_reported"),
    ],
)
def test_a_full_refund_needs_its_evidence_on_the_order(
    limits: Limits, reason: RefundReason, evidence: dict[str, Any], rule: str
) -> None:
    without = _refusal(ORDER_VALUE, reason, _case(), limits)
    with_evidence = _allowed(ORDER_VALUE, reason, _case(**evidence), limits)

    assert without == {"rule": rule, "cap_minor": 0, "requested_minor": ORDER_VALUE}
    assert with_evidence["cap_minor"] == ORDER_VALUE


def test_a_long_delay_or_a_lost_status_alone_is_not_a_confirmed_loss(limits: Limits) -> None:
    case = _case(status="lost", status_changed_at=ago(1000), carrier_exception_at=ago(900))

    assert _refusal(1000, "lost_in_transit", case, limits)["rule"] == "loss_not_confirmed"


def test_a_refund_never_exceeds_the_order_value_less_what_was_already_refunded(
    limits: Limits,
) -> None:
    case = _case(loss_confirmed=True, refunded_minor=70000)

    assert _allowed(ORDER_VALUE - 70000, "lost_in_transit", case, limits)["cap_minor"] == 550000
    assert _refusal(ORDER_VALUE - 69999, "lost_in_transit", case, limits) == {
        "rule": "exceeds_remaining",
        "cap_minor": 550000,
        "requested_minor": 550001,
    }


def test_nothing_is_left_to_refund_on_an_order_already_refunded_in_full(limits: Limits) -> None:
    case = _case(damage_reported=True, refunded_minor=ORDER_VALUE)

    assert _refusal(1, "damaged", case, limits)["cap_minor"] == 0


@pytest.mark.parametrize("reason", ["goodwill_delay", "lost_in_transit", "cancelled", "damaged"])
def test_no_second_request_is_taken_while_one_is_waiting(
    limits: Limits, reason: RefundReason
) -> None:
    case = _case(
        request_waiting=True, loss_confirmed=True, damage_reported=True, status="cancelled"
    )

    assert _refusal(1000, reason, case, limits) == {
        "rule": "request_waiting",
        "cap_minor": 0,
        "requested_minor": 1000,
    }


def test_a_refusal_says_nothing_about_the_customer(limits: Limits) -> None:
    with pytest.raises(PolicyViolationError) as refused:
        assess(10**9, "goodwill_delay", _case(), now=NOW, goodwill=limits.goodwill)

    body = refused.value.body()
    assert (body.code.value, body.retryable) == ("POLICY_VIOLATION", False)
    assert body.message == "Goodwill refund exceeds the 15% cap."


@pytest.mark.parametrize(
    ("already", "amount", "kind"),
    [
        (0, 1, "self"),
        (0, 500000, "self"),
        (0, 500001, "supervisor"),
        (0, 890000, "supervisor"),
        (500000, 1, "supervisor"),
        (300000, 200000, "self"),
        (300000, 200001, "supervisor"),
    ],
)
def test_the_requester_confirms_until_the_order_has_been_refunded_five_thousand_rupees_in_all(
    limits: Limits, already: int, amount: int, kind: str
) -> None:
    case = _case(refunded_minor=already)

    assert approval_kind(amount, case, limits.approval) == kind

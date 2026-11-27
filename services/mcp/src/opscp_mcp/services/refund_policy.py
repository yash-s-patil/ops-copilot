"""Whether a refund may be asked for. Plain arithmetic on facts: nothing here reads or writes.

The same check judges a refund request and, in advice, the amount an L1 engineer recommends
when escalating. A refusal names the rule, the most that could be asked for and what was
asked, so that an agent can correct itself.
"""

from datetime import datetime, timedelta
from typing import NoReturn

from opscp_common.errors import PolicyViolationError
from opscp_common.hashing import JsonValue
from opscp_common.limits import ApprovalLimits, GoodwillLimits
from opscp_common.vocabulary import GOODWILL_DELAY, RefundReason, TicketStatus
from opscp_mcp.ports.actions import ApprovalKind, RefundCase

_HOUR = timedelta(hours=1)
_REASONS: tuple[RefundReason, ...] = ("goodwill_delay", "lost_in_transit", "cancelled", "damaged")
_STATUSES: tuple[TicketStatus, ...] = ("open", "pending_customer", "resolved", "closed")
_PERCENT = 100


def delay_hours(case: RefundCase, now: datetime) -> int | None:
    """Whole hours an undelivered order has been held up, as the shipping policy measures it.

    A paid or packed order is delayed from the moment it entered that status. A shipped one
    from its carrier exception, or else from its last new scan, or else from pickup. An order
    that is delivered, cancelled, lost or not yet paid for has no delay.
    """
    if case.status in ("paid", "packed"):
        since = case.status_changed_at
    elif case.status == "shipped":
        since = case.carrier_exception_at or case.last_new_scan_at or case.status_changed_at
    else:
        return None
    return max((now - since) // _HOUR, 0)


def _refuse(
    message: str, *, rule: str, requested_minor: int, cap_minor: int = 0, **more: int
) -> NoReturn:
    raise PolicyViolationError(
        message, rule=rule, cap_minor=cap_minor, requested_minor=requested_minor, **more
    )


def _goodwill_cap(
    amount_minor: int, case: RefundCase, delay: int | None, remaining: int, limits: GoodwillLimits
) -> int:
    """The most a goodwill refund on this order may be, or a refusal."""
    rule = limits.premium if case.tier == "premium" else limits.standard
    cap = min(case.amount_minor * rule.max_percent // _PERCENT, remaining)
    if case.goodwill_given:
        _refuse(
            "A goodwill refund has already been made on this order.",
            rule="goodwill_already_given",
            requested_minor=amount_minor,
        )
    if delay is None:
        _refuse(
            "A goodwill refund is only for an order that is delayed and not yet delivered.",
            rule="goodwill_not_delayed",
            requested_minor=amount_minor,
        )
    if delay < rule.min_delay_hours:
        _refuse(
            f"The delay is {delay} hours; a goodwill refund needs {rule.min_delay_hours}.",
            rule="goodwill_delay_too_short",
            requested_minor=amount_minor,
            delay_hours=delay,
            min_delay_hours=rule.min_delay_hours,
        )
    if amount_minor > cap:
        _refuse(
            f"Goodwill refund exceeds the {rule.max_percent}% cap.",
            rule="goodwill_exceeds_cap",
            cap_minor=cap,
            requested_minor=amount_minor,
        )
    return cap


def _evidence_missing(reason: RefundReason, case: RefundCase) -> tuple[str, str] | None:
    """The rule and the message when the order does not show what a full refund needs."""
    if reason == "lost_in_transit" and not case.loss_confirmed:
        return "loss_not_confirmed", "The carrier has not confirmed that this order was lost."
    if reason == "cancelled" and case.status != "cancelled":
        return "not_cancelled", "This order has not been cancelled."
    if reason == "damaged" and not case.damage_reported:
        return "damage_not_reported", "No damage has been reported on this order."
    return None


def refuse_while_waiting(requested_minor: object) -> NoReturn:
    """A second refund request on an order is not taken until the first is settled."""
    _refuse(
        "Another refund request on this order is still waiting.",
        rule="request_waiting",
        requested_minor=requested_minor if isinstance(requested_minor, int) else 0,
    )


def assess(
    amount_minor: int,
    reason: RefundReason,
    case: RefundCase,
    *,
    now: datetime,
    goodwill: GoodwillLimits,
) -> dict[str, JsonValue]:
    """What the check saw, for the record. Raises PolicyViolationError when it refuses."""
    remaining = case.amount_minor - case.refunded_minor
    delay = delay_hours(case, now)
    if case.request_waiting:
        refuse_while_waiting(amount_minor)
    cap = remaining
    if reason == GOODWILL_DELAY:
        cap = _goodwill_cap(amount_minor, case, delay, remaining, goodwill)
    missing = _evidence_missing(reason, case)
    if missing is not None:
        _refuse(missing[1], rule=missing[0], requested_minor=amount_minor)
    if amount_minor > remaining:
        _refuse(
            "The refund is more than the order value less what has already been refunded.",
            rule="exceeds_remaining",
            cap_minor=remaining,
            requested_minor=amount_minor,
        )
    return {
        "order_amount_minor": case.amount_minor,
        "refunded_minor": case.refunded_minor,
        "tier": case.tier,
        "order_status": case.status,
        "delay_hours": delay,
        "cap_minor": cap,
    }


def reason_of(stored: object) -> RefundReason:
    """A stored reason code as one of the four, or a refusal: stored JSON is not typed."""
    for reason in _REASONS:
        if stored == reason:
            return reason
    raise PolicyViolationError("That is not a reason a refund can be given for.", rule="reason")


def status_of(stored: object) -> TicketStatus | None:
    """A stored status as one a ticket can have, None when none was asked for, or a refusal."""
    if stored is None:
        return None
    for status in _STATUSES:
        if stored == status:
            return status
    raise PolicyViolationError("That is not a status a ticket can have.", rule="status")


def approval_kind(amount_minor: int, case: RefundCase, limits: ApprovalLimits) -> ApprovalKind:
    """Up to the limit the requester confirms it; above it a supervisor must.

    The limit is on what the order will have been refunded in all, so that one refund cannot
    be split into several to stay under it.
    """
    total = case.refunded_minor + amount_minor
    return "self" if total <= limits.self_limit_minor else "supervisor"

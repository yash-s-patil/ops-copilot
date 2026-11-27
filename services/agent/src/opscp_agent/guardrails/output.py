"""G8: checks on what the model asks for and writes, made by ordinary code.

A change the model asks for is passed on only when it cites a tool result that was not
withheld, and, for a refund, only when the amount can be worked out from the order it
cites and the policy. A reply draft is shown only when it carries nobody's contact details
but the customer's own. These repeat rules the MCP server enforces; they are here so that
a wrong request is dropped before it is ever made.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from opscp_agent.jsonish import as_object
from opscp_agent.llm.base import Finding, ToolCall
from opscp_agent.mcp_client.tool_adapter import ISSUE_REFUND, UPDATE_TICKET, CallRecord, Facts
from opscp_common.limits import GoodwillLimits
from opscp_common.redaction import contacts_in

LAYER = "G8"
GOODWILL = "goodwill_delay"
PREMIUM = "premium"
_PERCENT = 100

type Severity = Literal["low", "medium", "high"]
type Action = Literal["stopped", "dropped", "withheld"]


@dataclass(frozen=True, slots=True, kw_only=True)
class Triggered:
    layer: str
    rule: str
    severity: Severity
    action: Action
    detail: str


def _usable(records: Sequence[CallRecord]) -> set[str]:
    """Provenance ids of results that came back whole: nothing in them was withheld."""
    return {
        record["provenance_id"]
        for record in records
        if record["status"] == "ok" and not record["quarantined"] and record["provenance_id"]
    }


def _asked_refund(call: ToolCall, facts: Facts, run_order_id: int | None) -> tuple[Any, ...] | None:
    """(order id, amount, reason) of the refund a call asks for or recommends, if any."""
    args = call["args"]
    if call["name"] == ISSUE_REFUND:
        return args.get("order_id"), args.get("amount_minor"), args.get("reason_code")
    if call["name"] == UPDATE_TICKET and isinstance(args.get("recommendation"), dict):
        advice = as_object(args["recommendation"])
        ticket = facts["tickets"].get(str(args.get("ticket_id")), {})
        order_id = ticket.get("order_id", run_order_id)
        return order_id, advice.get("amount_minor"), advice.get("reason_code")
    return None


def refund_cap(
    order: dict[str, Any], tier: object, reason: object, goodwill: GoodwillLimits
) -> int:
    """The most that may be refunded on this order for this reason.

    A customer whose tier is not known is treated as standard, the lower of the two caps.
    """
    amount, refunded = order.get("amount_minor"), order.get("refunded_minor")
    if not isinstance(amount, int) or not isinstance(refunded, int):
        return 0
    left = max(amount - refunded, 0)
    if reason != GOODWILL:
        return left
    rule = goodwill.premium if tier == PREMIUM else goodwill.standard
    return min(left, amount * rule.max_percent // _PERCENT)


def _refused(
    call: ToolCall,
    cited: Collection[str],
    facts: Facts,
    run_order_id: int | None,
    goodwill: GoodwillLimits,
) -> Triggered | None:
    asked = _asked_refund(call, facts, run_order_id)
    if asked is None:
        return None
    order_id, amount, reason = asked
    order = facts["orders"].get(str(order_id))
    if order is None or order.get("provenance_id") not in cited:
        return Triggered(
            layer=LAYER,
            rule="amount_not_derivable",
            severity="medium",
            action="dropped",
            detail="A refund was asked for without citing the order it is for.",
        )
    tier = facts["customers"].get(str(order.get("customer_id")), {}).get("tier")
    cap = refund_cap(order, tier, reason, goodwill)
    if not isinstance(amount, int) or isinstance(amount, bool) or not 0 < amount <= cap:
        return Triggered(
            layer=LAYER,
            rule="amount_over_cap",
            severity="medium",
            action="dropped",
            detail=f"A refund outside what this order allows (at most {cap} paise) was asked for.",
        )
    return None


def check_actions(
    calls: Sequence[ToolCall],
    finding: Finding | None,
    records: Sequence[CallRecord],
    facts: Facts,
    *,
    run_order_id: int | None,
    goodwill: GoodwillLimits,
) -> tuple[list[ToolCall], list[tuple[ToolCall, Triggered]]]:
    """The calls that may be passed on, and those dropped with the reason for each."""
    usable = _usable(records)
    cited_by = {
        action.call_id: set(action.evidence) & usable
        for action in (finding.proposed_actions if finding else ())
    }
    kept: list[ToolCall] = []
    dropped: list[tuple[ToolCall, Triggered]] = []
    for call in calls:
        cited = cited_by.get(call["id"], set())
        refusal = (
            Triggered(
                layer=LAYER,
                rule="no_evidence",
                severity="medium",
                action="dropped",
                detail="A change was asked for without citing a tool result that supports it.",
            )
            if not cited
            else _refused(call, cited, facts, run_order_id, goodwill)
        )
        if refusal is None:
            kept.append(call)
        else:
            dropped.append((call, refusal))
    return kept, dropped


def check_reply(draft: str, facts: Facts, customer_id: object) -> Triggered | None:
    """Triggered when the draft carries contact details other than the customer's own."""
    customer = facts["customers"].get(str(customer_id), {})
    own = frozenset[str]().union(
        *(
            contacts_in(value)
            for value in (customer.get("email"), customer.get("phone"))
            if isinstance(value, str)
        )
    )
    if contacts_in(draft) <= own:
        return None
    return Triggered(
        layer=LAYER,
        rule="reply_contact_details",
        severity="medium",
        action="withheld",
        detail="The reply draft held contact details that are not the customer's own.",
    )


def canary_leak(where: str) -> Triggered:
    return Triggered(
        layer=LAYER,
        rule="canary",
        severity="high",
        action="stopped",
        detail=f"The canary appeared in {where}.",
    )

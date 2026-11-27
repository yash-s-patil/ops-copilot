"""Turns tool results into what the rest of the agent keeps: a record and a few facts.

The facts are the numbers the output checks need (an order's amount, a customer's tier).
They are read from structured fields only, never from free text.
"""

from typing import Any, TypedDict

from opscp_agent.jsonish import as_list, as_object
from opscp_agent.mcp_client.types import CallStatus, Json, ToolOutcome

GET_ORDER = "get_order"
LOOKUP_CUSTOMER = "lookup_customer"
LIST_TICKETS = "list_tickets"
GET_TICKET = "get_ticket"
SEARCH_RUNBOOKS = "search_runbooks"
GET_POLICY_DOCUMENT = "get_policy_document"
ISSUE_REFUND = "issue_refund"
UPDATE_TICKET = "update_ticket"
IDEMPOTENCY_KEY = "idempotency_key"
APPROVAL_TOKEN = "approval_token"  # noqa: S105 -- an argument name
APPROVAL_REQUIRED = "approval_required"


class CallRecord(TypedDict):
    """One tool call of a run, without its result. The result is in the messages."""

    call_id: str
    tool: str
    phase: str
    status: CallStatus
    error_code: str | None
    provenance_id: str | None
    quarantined: bool
    summary: str
    duration_ms: int
    size_bytes: int


class Facts(TypedDict):
    """Keyed by number written as text, since the state is stored as JSON."""

    orders: dict[str, Json]
    customers: dict[str, Json]
    tickets: dict[str, Json]


def no_facts() -> Facts:
    return Facts(orders={}, customers={}, tickets={})


def any_quarantined(value: Any) -> bool:
    """Whether the scanner withheld any part of this result."""
    held = as_object(value)
    return (
        held.get("quarantined") is True
        or any(any_quarantined(item) for item in held.values())
        or any(any_quarantined(item) for item in as_list(value))
    )


def record_of(
    call_id: str, tool: str, phase: str, outcome: ToolOutcome, summary: str
) -> CallRecord:
    return CallRecord(
        call_id=call_id,
        tool=tool,
        phase=phase,
        status=outcome.status,
        error_code=None if outcome.error is None else outcome.error.code.value,
        provenance_id=outcome.provenance_id,
        quarantined=any_quarantined(outcome.data),
        summary=summary,
        duration_ms=outcome.duration_ms,
        size_bytes=outcome.size_bytes,
    )


def any_truncated(value: Any) -> bool:
    """Whether the server cut any text of this result short."""
    return any(
        (key.endswith("truncated") and item is True) or any_truncated(item)
        for key, item in as_object(value).items()
    ) or any(any_truncated(item) for item in as_list(value))


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def learn(facts: Facts, tool: str, outcome: ToolOutcome) -> Facts:
    """The facts with what this result adds. Results that are not as expected add nothing."""
    data = outcome.data
    if outcome.status != "ok" or data is None:
        return facts
    orders, customers, tickets = (
        dict(facts["orders"]),
        dict(facts["customers"]),
        dict(facts["tickets"]),
    )
    if tool == GET_ORDER and _int(data.get("order_id")) is not None:
        orders[str(data["order_id"])] = {
            "amount_minor": _int(data.get("amount_minor")),
            "refunded_minor": _int(data.get("refunded_minor")),
            "customer_id": _int(data.get("customer_id")),
            "provenance_id": outcome.provenance_id,
        }
    if tool == LOOKUP_CUSTOMER and _int(data.get("customer_id")) is not None:
        customers[str(data["customer_id"])] = {
            "tier": data.get("tier"),
            "email": data.get("email"),
            "phone": data.get("phone"),
        }
    if tool == GET_TICKET and _int(data.get("ticket_id")) is not None:
        tickets[str(data["ticket_id"])] = {
            "order_id": _int(data.get("order_id")),
            "customer_id": _int(data.get("customer_id")),
        }
    return Facts(orders=orders, customers=customers, tickets=tickets)

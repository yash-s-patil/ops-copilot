"""The one plain sentence an engineer sees when they open a step.

Each tool has a fixed formatter that reads structured fields of the result. The model never
writes a summary, a summary never holds withheld text, and a summary is never given to the
model.
"""

from collections.abc import Callable, Mapping

from opscp_agent.jsonish import as_list, as_object
from opscp_agent.mcp_client.tool_adapter import (
    GET_ORDER,
    GET_POLICY_DOCUMENT,
    GET_TICKET,
    ISSUE_REFUND,
    LIST_TICKETS,
    LOOKUP_CUSTOMER,
    SEARCH_RUNBOOKS,
    UPDATE_TICKET,
)
from opscp_agent.mcp_client.types import Json, ToolOutcome
from opscp_common.errors import ErrorCode

PREPARED = "Prepared only. Nothing changes until you approve."
DONE = "Done, after approval."
WITHHELD = "A result was withheld as suspicious."
_MINOR_PER_UNIT = 100
_ELLIPSIS = "…"

_FAILURES: Mapping[ErrorCode, str] = {
    ErrorCode.PERMISSION_DENIED: (
        "Your role does not include this action. The server refused it and logged the attempt."
    ),
    ErrorCode.NOT_FOUND: "Nothing with that number is visible to you.",
    ErrorCode.VALIDATION_FAILED: "The request was not one the server accepts.",
    ErrorCode.RATE_LIMITED: "Too many requests just now. Try again in a minute.",
    ErrorCode.BACKEND_UNAVAILABLE: "This is unavailable right now. Continuing without it.",
    ErrorCode.TIMEOUT: "This took too long to answer. Continuing without it.",
    ErrorCode.APPROVAL_INVALID: "The approval could not be used.",
    ErrorCode.IDEMPOTENCY_CONFLICT: "This change was already asked for in a different form.",
    ErrorCode.INTERNAL: "Something went wrong on the server.",
}


def money(amount_minor: object, currency: object = "INR") -> str:
    if not isinstance(amount_minor, int) or isinstance(amount_minor, bool):
        return "an amount"
    return f"{currency} {amount_minor // _MINOR_PER_UNIT:,}.{amount_minor % _MINOR_PER_UNIT:02d}"


def _words(value: object) -> str:
    return str(value).replace("_", " ")


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _order(data: Json) -> str:
    start = f"{_words(data.get('status')).capitalize()} for {data.get('status_age_hours')} hours."
    recent = as_list(as_object(data.get("events")).get("recent"))
    if not recent:
        return start
    return f"{start} Last event: {_words(as_object(recent[0]).get('type'))}."


def _customer(data: Json) -> str:
    return f"{data.get('display_name')}, {data.get('tier')} customer."


def _tickets(data: Json) -> str:
    items = as_list(data.get("items"))
    if not items:
        return "No ticket found."
    if len(items) == 1:
        only = as_object(items[0])
        return f"Ticket #{only.get('ticket_id')}, priority {only.get('priority')}."
    return f"{_plural(len(items), 'ticket')} found."


def _ticket(data: Json) -> str:
    notes = as_list(data.get("notes"))
    return f"{_plural(len(notes), 'note')} on the ticket."


def _runbooks(data: Json) -> str:
    results = [as_object(hit) for hit in as_list(data.get("results"))]
    titles = [str(hit.get("title")) for hit in results if hit.get("quarantined") is not True]
    held_back = len(results) - len(titles)
    found = f"Matches: {'; '.join(titles)}." if titles else "No runbook matched."
    return f"{found} {WITHHELD}" if held_back else found


def _policy(data: Json) -> str:
    section = as_object(data.get("section"))
    if section:
        return WITHHELD if section.get("quarantined") is True else str(section.get("content", ""))
    sections = as_list(data.get("sections"))
    return f"{_plural(len(sections), 'section')} in the document."


_READS: Mapping[str, Callable[[Json], str]] = {
    GET_ORDER: _order,
    LOOKUP_CUSTOMER: _customer,
    LIST_TICKETS: _tickets,
    GET_TICKET: _ticket,
    SEARCH_RUNBOOKS: _runbooks,
    GET_POLICY_DOCUMENT: _policy,
}


def _failure(outcome: ToolOutcome) -> str:
    error = outcome.error
    if error is None:
        return _FAILURES[ErrorCode.INTERNAL]
    if error.code == ErrorCode.POLICY_VIOLATION:
        cap = error.details.get("cap_minor")
        most = f" The most that can be asked for is {money(cap)}." if isinstance(cap, int) else ""
        return f"Refused by policy.{most}"
    return _FAILURES[error.code]


def step_summary(tool: str, outcome: ToolOutcome, max_chars: int) -> str:
    """What one finished step found, in a sentence a support engineer can read."""
    if outcome.status != "ok" or outcome.data is None:
        text = _failure(outcome)
    elif tool in (ISSUE_REFUND, UPDATE_TICKET):
        text = PREPARED if outcome.data.get("status") == "approval_required" else DONE
    elif tool in _READS:
        text = _READS[tool](outcome.data)
    else:
        text = "Done."
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + _ELLIPSIS

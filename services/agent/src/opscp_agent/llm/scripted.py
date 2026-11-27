"""A stand-in for the model on a developer's machine: the same investigation, every time.

It follows the server's `investigate_order` procedure by fixed rules, reading the same tool
results a model would and asking for the same kinds of change. It makes local development
and the tests repeatable; it says nothing about how a real model behaves, and it never
runs in production. The numbers in it are this script's own, not Meridian's policy: the
server's policy check is what decides.
"""

import re
from typing import Any

from opscp_agent.events.summaries import money
from opscp_agent.guardrails.input import order_named_in
from opscp_agent.guardrails.spotlight import read_back
from opscp_agent.jsonish import as_list, as_object
from opscp_agent.llm.base import (
    Evidence,
    Finding,
    Message,
    ModelRequest,
    ModelTurn,
    ProposedAction,
    ToolCall,
    Usage,
)
from opscp_agent.mcp_client import tool_adapter as tool

NAME = "scripted"
_CHARS_PER_TOKEN = 4
_TICKET = re.compile(r"\bticket\s*#?\s*(\d{1,15})\b", re.IGNORECASE)
_GOODWILL_PERCENT = 10
_DELAY_HOURS = {"premium": 72, "standard": 120}
_DELAYED_STATUSES = ("paid", "packed", "shipped")
_REFUND_POLICY = "refund-policy"
_GOODWILL_SECTION = "goodwill"
_REJECTED = "The engineer rejected the proposed changes."


class _Read:
    """What the conversation so far shows: each tool's latest answer, by tool name."""

    def __init__(self, messages: tuple[Message, ...]) -> None:
        self.asked: dict[str, str] = {}
        self.results: dict[str, dict[str, Any]] = {}
        self.sources: dict[str, str] = {}
        self.errors: dict[str, dict[str, Any]] = {}
        self.said = " ".join(m["content"] for m in messages if m["role"] == "user")
        self.rejected = _REJECTED in self.said
        self.turns = sum(1 for message in messages if message["role"] == "assistant")
        for message in messages:
            for call in message.get("tool_calls", []):
                self.asked[call["id"]] = call["name"]
            if message["role"] != "tool":
                continue
            name = self.asked.get(message.get("tool_call_id", ""), "")
            body = read_back(message["content"])
            if body is None:
                continue
            if "error" in body or "not_done" in body:
                self.errors[name] = body.get("error") or {"message": body.get("not_done")}
            else:
                self.results[name] = body
                self.sources[name] = _source(message["content"])

    def tried(self, name: str) -> bool:
        return name in self.results or name in self.errors


def _source(block: str) -> str:
    found = re.match(r'<<tool_result source="([^"]+)"', block)
    return found.group(1) if found else ""


def _number(pattern: re.Pattern[str], text: str) -> int | None:
    found = pattern.search(text)
    return None if found is None else int(found.group(1))


class ScriptedModel:
    @property
    def name(self) -> str:
        return NAME

    async def complete(self, request: ModelRequest) -> ModelTurn:
        read = _Read(request.messages)
        if request.purpose == "reply":
            return self._turn(request, _reply(read))
        available = {spec.name for spec in request.tools}
        calls = _next_reads(read, available)
        if calls:
            numbered = [
                ToolCall(id=f"call-{read.turns + 1}-{index}", name=name, args=args)
                for index, (name, args) in enumerate(calls, start=1)
            ]
            return self._turn(request, "", tuple(numbered))
        text, finding, writes = _conclusion(read, available)
        return self._turn(request, text, tuple(writes), finding)

    def _turn(
        self,
        request: ModelRequest,
        text: str,
        calls: tuple[ToolCall, ...] = (),
        finding: Finding | None = None,
    ) -> ModelTurn:
        read_chars = len(request.system) + sum(len(m["content"]) for m in request.messages)
        return ModelTurn(
            text=text,
            tool_calls=calls,
            finding=finding,
            usage=Usage(
                tokens_in=read_chars // _CHARS_PER_TOKEN,
                tokens_out=max(len(text) // _CHARS_PER_TOKEN, 1),
            ),
        )


def _next_reads(read: _Read, available: set[str]) -> list[tuple[str, dict[str, Any]]]:
    """The procedure's next step that has not been tried and that this person may use."""
    order_id = order_named_in(read.said)
    if order_id is None or read.rejected:
        return []
    order = read.results.get(tool.GET_ORDER)
    if not read.tried(tool.GET_ORDER):
        return _allowed([(tool.GET_ORDER, {"order_id": order_id})], available)
    if order is None:
        return []
    ticket_id = _number(_TICKET, read.said)
    second = [
        (tool.LOOKUP_CUSTOMER, {"customer_id": order.get("customer_id")}),
        (tool.LIST_TICKETS, {"order_id": order_id}),
    ]
    untried = [step for step in second if not read.tried(step[0])]
    if _allowed(untried, available):
        return _allowed(untried, available)
    ticket_id = ticket_id or _first_ticket(read)
    if ticket_id is not None and not read.tried(tool.GET_TICKET):
        found = _allowed([(tool.GET_TICKET, {"ticket_id": ticket_id})], available)
        if found:
            return found
    last = [
        (tool.SEARCH_RUNBOOKS, {"query": _search_words(order)}),
        (tool.GET_POLICY_DOCUMENT, {"name": _REFUND_POLICY, "section": _GOODWILL_SECTION}),
    ]
    return _allowed([step for step in last if not read.tried(step[0])], available)


def _allowed(
    steps: list[tuple[str, dict[str, Any]]], available: set[str]
) -> list[tuple[str, dict[str, Any]]]:
    return [step for step in steps if step[0] in available]


def _first_ticket(read: _Read) -> int | None:
    items = as_list(read.results.get(tool.LIST_TICKETS, {}).get("items"))
    found = as_object(items[0]).get("ticket_id") if items else None
    return found if isinstance(found, int) else None


def _last_event(order: dict[str, Any]) -> str:
    recent = as_list(as_object(order.get("events")).get("recent"))
    return str(as_object(recent[0]).get("type", "")).replace("_", " ") if recent else ""


def _search_words(order: dict[str, Any]) -> str:
    words = f"{_last_event(order)} order stuck in {order.get('status')}".strip()
    return words if _last_event(order) else f"order stuck in {order.get('status')}"


def _conclusion(read: _Read, available: set[str]) -> tuple[str, Finding, list[ToolCall]]:
    order_id = order_named_in(read.said)
    if order_id is None:
        text = "Tell me which order this is about, for example: order 4471."
        return text, Finding(root_cause=text, confidence="low"), []
    order = read.results.get(tool.GET_ORDER)
    if order is None:
        failure = read.errors.get(tool.GET_ORDER, {})
        text = (
            f"I cannot find order {order_id}."
            if failure.get("code") == "NOT_FOUND"
            else f"I could not read order {order_id}, so I cannot say what is wrong with it."
        )
        return text, Finding(root_cause=text, confidence="low"), []
    cause = (
        f"Order {order_id} has been {order.get('status')} for "
        f"{order.get('status_age_hours')} hours."
    )
    if _last_event(order):
        cause += f" The last event was {_last_event(order)}."
    evidence = [Evidence(provenance_id=read.sources[tool.GET_ORDER], claim=cause)]
    caveats = [
        f"{name.replace('_', ' ')} was not available."
        for name in (tool.SEARCH_RUNBOOKS, tool.GET_POLICY_DOCUMENT, tool.LOOKUP_CUSTOMER)
        if name in read.errors
    ]
    if read.rejected:
        caveats.append("The proposed changes were rejected, so nothing was changed.")
        return (
            cause,
            Finding(root_cause=cause, evidence=tuple(evidence), caveats=tuple(caveats)),
            [],
        )
    amount = _goodwill(read, order)
    if amount is None:
        text = f"{cause} It does not qualify for a goodwill refund as things stand."
        finding = Finding(root_cause=cause, evidence=tuple(evidence), caveats=tuple(caveats))
        return text, finding, []
    writes, said = _changes(read, available, order_id, amount)
    cited = tuple(
        read.sources[name]
        for name in (tool.GET_ORDER, tool.LOOKUP_CUSTOMER, tool.GET_POLICY_DOCUMENT)
        if name in read.sources
    )
    finding = Finding(
        root_cause=cause,
        evidence=tuple(evidence),
        proposed_actions=tuple(ProposedAction(call_id=w["id"], evidence=cited) for w in writes),
        confidence="high" if not caveats else "medium",
        caveats=tuple(caveats),
    )
    return f"{cause} {said}", finding, writes


def _goodwill(read: _Read, order: dict[str, Any]) -> int | None:
    """The refund this script asks for, or None when the order does not look delayed."""
    if tool.ISSUE_REFUND in read.errors or tool.UPDATE_TICKET in read.errors:
        return None
    ticket = read.results.get(tool.GET_TICKET, {})
    if ticket.get("refund") is not None or order.get("refunded_minor"):
        return None
    advised = as_object(ticket.get("recommendation")).get("amount_minor")
    if isinstance(advised, int):
        return advised
    tier = read.results.get(tool.LOOKUP_CUSTOMER, {}).get("tier", "standard")
    delayed = order.get("status") in _DELAYED_STATUSES and order.get(
        "status_age_hours", 0
    ) >= _DELAY_HOURS.get(tier, _DELAY_HOURS["standard"])
    return order.get("amount_minor", 0) * _GOODWILL_PERCENT // 100 if delayed else None


def _changes(
    read: _Read, available: set[str], order_id: int, amount: int
) -> tuple[list[ToolCall], str]:
    ticket = read.results.get(tool.GET_TICKET, {})
    ticket_id = ticket.get("ticket_id")
    turn = read.turns + 1
    why = f"Delayed order: goodwill refund of {money(amount)} under the refund policy."
    note = f"Copilot investigation: order {order_id} is delayed. {why}"
    if tool.ISSUE_REFUND in available:
        refund: dict[str, Any] = {
            "order_id": order_id,
            "amount_minor": amount,
            "reason_code": "goodwill_delay",
            "reason_text": why,
        }
        writes = [ToolCall(id=f"call-{turn}-1", name=tool.ISSUE_REFUND, args=refund)]
        if ticket_id is not None:
            refund["ticket_id"] = ticket_id
            if tool.UPDATE_TICKET in available:
                writes.append(
                    ToolCall(
                        id=f"call-{turn}-2",
                        name=tool.UPDATE_TICKET,
                        args={"ticket_id": ticket_id, "note": note},
                    )
                )
        return writes, f"A goodwill refund of {money(amount)} looks justified."
    if tool.UPDATE_TICKET in available and ticket_id is not None and ticket.get("queue") == "L1":
        escalation = {
            "ticket_id": ticket_id,
            "note": note,
            "escalate_to": "L2",
            "recommendation": {
                "amount_minor": amount,
                "reason_code": "goodwill_delay",
                "reason_text": why,
            },
        }
        said = (
            f"A goodwill refund of {money(amount)} looks justified, but a refund needs an "
            "L2 engineer. I can escalate the ticket with that recommendation."
        )
        return [ToolCall(id=f"call-{turn}-1", name=tool.UPDATE_TICKET, args=escalation)], said
    return [], (
        f"A goodwill refund of {money(amount)} looks justified, but your role does not "
        "include refunds or ticket changes."
    )


def _reply(read: _Read) -> str:
    order = read.results.get(tool.GET_ORDER, {})
    name = read.results.get(tool.LOOKUP_CUSTOMER, {}).get("display_name", "there")
    return (
        f"Hello {name},\n\n"
        f"I am sorry for the delay with order {order.get('order_id')}. It is currently "
        f"{order.get('status')}, and I have looked into what held it up. We are putting it "
        "right and I will stay on it until it moves.\n\n"
        "If anything is still wrong, reply to this message and it will come straight to me."
    )

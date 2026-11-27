"""reply: drafts what the engineer might send the customer, and closes the run.

The draft is a draft. Nothing here sends anything, and a draft that carries contact details
other than the customer's own is not shown.
"""

import time
from typing import Any

from langgraph.runtime import Runtime

from opscp_agent.events import types as events
from opscp_agent.graph import names
from opscp_agent.graph.context import RunContext
from opscp_agent.graph.nodes.execute import EXECUTED
from opscp_agent.graph.nodes.reason import request_for
from opscp_agent.graph.state import ActionOutcome, RunState
from opscp_agent.graph.steps import spent
from opscp_agent.guardrails.budget import Budget, exceeded
from opscp_agent.guardrails.canary import leaked
from opscp_agent.guardrails.output import canary_leak, check_reply
from opscp_agent.llm.base import ModelUnavailableError
from opscp_agent.mcp_client.tool_adapter import ISSUE_REFUND, UPDATE_TICKET
from opscp_agent.mcp_client.types import McpCallError, McpUnavailableError
from opscp_common.redaction import without_contacts

DRAFT_CUSTOMER_REPLY = "draft_customer_reply"
APOLOGETIC = "apologetic"
NEUTRAL = "neutral"
NO_DRAFT = "A reply to the customer could not be drafted."
_MS_PER_S = 1000


def _made(actions: list[ActionOutcome], tool: str) -> list[ActionOutcome]:
    return [action for action in actions if action["tool"] == tool and action["status"] == EXECUTED]


def only_escalated(actions: list[ActionOutcome]) -> bool:
    """Whether all that was done was to hand the ticket to the next queue."""
    escalations = [action for action in _made(actions, UPDATE_TICKET) if action["escalates"]]
    return bool(escalations) and not _made(actions, ISSUE_REFUND)


async def _draft(
    state: RunState, context: RunContext, *, refunded: bool
) -> tuple[str | None, list[str], list[dict[str, Any]], Budget]:
    """The draft, or None with the reason it is missing, and what the attempt cost."""
    began = time.monotonic()
    budget = state["budget"]
    ticket_id = state["ticket_id"]
    if ticket_id is None:
        return None, [], [], budget
    if exceeded(budget, context.limits.budget) is not None:
        return None, [NO_DRAFT], [], budget
    tone = APOLOGETIC if refunded else NEUTRAL
    try:
        brief = await context.mcp.prompt(
            DRAFT_CUSTOMER_REPLY, {"ticket_id": str(ticket_id), "tone": tone}
        )
        turn = await context.model.complete(request_for(state, context, "reply", brief))
    except (McpCallError, McpUnavailableError, ModelUnavailableError):
        return None, [NO_DRAFT], [], budget
    latency_ms = round((time.monotonic() - began) * _MS_PER_S)
    await context.journal.model_call(names.REPLY, context.model.name, turn.usage, latency_ms)
    budget = spent(
        budget,
        began,
        tokens_in=budget["tokens_in"] + turn.usage.tokens_in,
        tokens_out=budget["tokens_out"] + turn.usage.tokens_out,
        tokens_cached=budget["tokens_cached"] + turn.usage.tokens_cached,
        tokens_waiting=0,
        model_turns=budget["model_turns"] + 1,
    )
    await context.journal.budget(budget, context.model.name)
    ticket = state["facts"]["tickets"].get(str(ticket_id), {})
    triggered = (
        canary_leak("the reply draft")
        if leaked(context.canary, turn.text)
        else check_reply(turn.text, state["facts"], ticket.get("customer_id"))
    )
    if triggered is not None:
        logged = await context.journal.guardrail(triggered, f"reply:{triggered.rule}")
        return None, [NO_DRAFT], [logged], budget
    return turn.text.strip() or None, [], [], budget


def result_of(
    state: RunState, actions: list[ActionOutcome], draft: str | None, caveats: list[str]
) -> dict[str, Any]:
    """The structured answer of a run, from what the model found and what was done.

    The model's own words are kept without anyone's contact details. Other engineers are
    shown the cause of a ticket's investigations, and one of them may hold a role that
    sees contact details masked.
    """
    finding = state["finding"] or {}
    known = {record["provenance_id"] for record in state["tool_calls"]}
    escalated = only_escalated(actions)
    cause = finding.get("root_cause") or (state["turn"] or {}).get("text") or ""
    return {
        "root_cause": without_contacts(cause),
        "evidence": [
            item | {"claim": without_contacts(str(item.get("claim", "")))}
            for item in finding.get("evidence", [])
            if item.get("provenance_id") in known
        ],
        "proposed_actions": actions,
        "escalated": escalated,
        "customer_reply_draft": None if escalated else draft,
        "confidence": finding.get("confidence", "low"),
        "caveats": [
            *(without_contacts(str(caveat)) for caveat in finding.get("caveats", [])),
            *state["caveats"],
            *caveats,
        ],
    }


async def reply(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    context = runtime.context
    actions = state["actions"]
    draft: str | None = None
    caveats: list[str] = []
    guardrails: list[dict[str, Any]] = []
    budget = state["budget"]
    if not only_escalated(actions):
        draft, caveats, guardrails, budget = await _draft(
            state, context, refunded=bool(_made(actions, ISSUE_REFUND))
        )
    result = result_of(state, actions, draft, caveats)
    await context.journal.emit(events.RUN_COMPLETED, "run.completed", result)
    return {
        "result": result,
        "status": "completed",
        "budget": budget,
        "guardrail_events": guardrails,
    }

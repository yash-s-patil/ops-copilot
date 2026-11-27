"""reason: one call of the model, if the run may still afford it."""

import time
from typing import Any

from langgraph.runtime import Runtime
from langgraph.types import Command

from opscp_agent.events import types as events
from opscp_agent.graph import names
from opscp_agent.graph.context import RunContext
from opscp_agent.graph.prompts import system_prompt
from opscp_agent.graph.state import RunState
from opscp_agent.graph.steps import spent
from opscp_agent.guardrails.budget import exceeded
from opscp_agent.guardrails.canary import leaked
from opscp_agent.guardrails.output import Triggered, canary_leak
from opscp_agent.llm.base import Message, ModelRequest, ModelTurn, ToolCall
from opscp_agent.mcp_client.types import ToolSpec

_MS_PER_S = 1000
G9 = "G9"


def request_for(
    state: RunState, context: RunContext, purpose: Any, extra: str = ""
) -> ModelRequest:
    system = system_prompt(
        state["resources"],
        "\n\n".join(part for part in (state["procedure"], extra) if part),
        now=context.now(),
        user_name=context.person.name,
        user_roles=context.person.roles,
        canary=context.canary,
    )
    return ModelRequest(
        purpose=purpose,
        system=system,
        messages=tuple(state["messages"]),
        tools=tuple(ToolSpec(**tool) for tool in state["tools"]),
    )


async def over_budget(state: RunState, context: RunContext, reason: str) -> Command[str]:
    triggered = Triggered(
        layer=G9, rule="budget", severity="medium", action="stopped", detail=reason
    )
    logged = await context.journal.guardrail(triggered, f"budget:{state['budget']['model_turns']}")
    return Command(
        goto=names.STOP,
        update={"stop_reason": reason, "stop_code": names.BUDGET, "guardrail_events": [logged]},
    )


def _numbered(turn: ModelTurn, number: int) -> tuple[list[ToolCall], dict[str, Any] | None]:
    """The turn's calls under ids the agent gives, and its finding pointing at those ids.

    A model's own ids are not relied on: events and idempotency keys are named after a
    call's id, so an id used twice would hide one call behind another.
    """
    given = {call["id"]: f"call-{number}-{n}" for n, call in enumerate(turn.tool_calls, start=1)}
    calls = [
        ToolCall(id=f"call-{number}-{n}", name=call["name"], args=call["args"])
        for n, call in enumerate(turn.tool_calls, start=1)
    ]
    if turn.finding is None:
        return calls, None
    finding = turn.finding.model_dump(mode="json")
    for action in finding["proposed_actions"]:
        action["call_id"] = given.get(action["call_id"], action["call_id"])
    return calls, finding


async def reason(state: RunState, runtime: Runtime[RunContext]) -> Command[str]:
    began = time.monotonic()
    context = runtime.context
    refusal = exceeded(state["budget"], context.limits.budget)
    if refusal is not None:
        return await over_budget(state, context, refusal)
    turn = await context.model.complete(request_for(state, context, "investigate"))
    latency_ms = round((time.monotonic() - began) * _MS_PER_S)
    await context.journal.model_call(names.REASON, context.model.name, turn.usage, latency_ms)
    before = state["budget"]
    budget = spent(
        before,
        began,
        tokens_in=before["tokens_in"] + turn.usage.tokens_in,
        tokens_out=before["tokens_out"] + turn.usage.tokens_out,
        tokens_cached=before["tokens_cached"] + turn.usage.tokens_cached,
        tokens_waiting=0,
        model_turns=before["model_turns"] + 1,
    )
    calls, finding = _numbered(turn, budget["model_turns"])
    if leaked(context.canary, turn.text, finding, calls):
        logged = await context.journal.guardrail(
            canary_leak("the model's answer"), f"canary:{budget['model_turns']}"
        )
        return Command(
            goto=names.STOP,
            update={
                "budget": budget,
                "guardrail_events": [logged],
                "stop_code": names.GUARDRAIL,
                "stop_reason": "The assistant's answer broke a safety rule, so it was not used.",
            },
        )
    if turn.text:
        await context.journal.emit(
            events.MODEL_DELTA,
            f"model.delta:{budget['model_turns']}:{context.journal.attempt}",
            {
                "turn": budget["model_turns"],
                "attempt": context.journal.attempt,
                "text": turn.text,
            },
        )
    await context.journal.budget(budget, context.model.name)
    message = Message(role="assistant", content=turn.text)
    if calls:
        message["tool_calls"] = calls
    return Command(
        goto=names.GATE,
        update={
            "messages": [message],
            "turn": {"text": turn.text, "tool_calls": calls, "finding": finding},
            "finding": finding if finding is not None else state["finding"],
            "budget": budget,
        },
    )

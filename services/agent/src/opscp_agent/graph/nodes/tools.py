"""tools: runs what the model asked to read, all at once. Nothing here changes anything."""

import asyncio
import time
from collections.abc import Mapping

from langgraph.runtime import Runtime
from langgraph.types import Command

from opscp_agent.graph import names
from opscp_agent.graph.context import RunContext
from opscp_agent.graph.nodes.gate import split
from opscp_agent.graph.state import RunState
from opscp_agent.graph.steps import (
    READ,
    call_once_more_if_passing,
    finished,
    spent,
    started,
    tool_message,
)
from opscp_agent.guardrails.budget import estimated_tokens
from opscp_agent.guardrails.spotlight import spotlighted
from opscp_agent.llm.base import Message, ToolCall
from opscp_agent.mcp_client import tool_adapter as tools_known
from opscp_agent.mcp_client.tool_adapter import CallRecord, learn
from opscp_agent.mcp_client.types import ToolOutcome
from opscp_common.errors import ErrorCode

NOT_YET = "Not done. Ask for a change only in a turn that reads nothing."
NOT_YOURS = "Not done. This tool is not available to the signed-in person."
_UNCHECKED: Mapping[str, str] = {
    tools_known.GET_ORDER: "The order could not be read.",
    tools_known.LOOKUP_CUSTOMER: "The customer could not be looked up.",
    tools_known.LIST_TICKETS: "Tickets could not be listed.",
    tools_known.GET_TICKET: "The ticket could not be read.",
    tools_known.SEARCH_RUNBOOKS: "Runbooks could not be checked.",
    tools_known.GET_POLICY_DOCUMENT: "The policy could not be read.",
}


async def _read(context: RunContext, call: ToolCall) -> tuple[ToolOutcome, CallRecord]:
    await started(context, call, READ)
    outcome = await call_once_more_if_passing(context, call, call["args"])
    return outcome, await finished(context, call, READ, outcome)


async def tools(state: RunState, runtime: Runtime[RunContext]) -> Command[str]:
    began = time.monotonic()
    context = runtime.context
    asked = split(state)
    reads = asked.reads
    done = await asyncio.gather(*(_read(context, call) for call in reads))
    facts, invalid = state["facts"], dict(state["invalid_calls"])
    messages: list[Message] = []
    caveats: list[str] = []
    for call, (outcome, _record) in zip(reads, done, strict=True):
        facts = learn(facts, call["name"], outcome)
        messages.append(tool_message(call, outcome))
        if outcome.error is None:
            continue
        if outcome.error.code == ErrorCode.VALIDATION_FAILED:
            invalid[call["name"]] = invalid.get(call["name"], 0) + 1
        if outcome.error.retryable and call["name"] in _UNCHECKED:
            caveats.append(_UNCHECKED[call["name"]])
    messages += [
        Message(
            role="tool",
            content=spotlighted("agent", {"not_done": reason}),
            tool_call_id=call["id"],
        )
        for unrun, reason in ((asked.writes, NOT_YET), (asked.unlisted, NOT_YOURS))
        for call in unrun
    ]
    waiting = sum(
        estimated_tokens(message["content"], context.limits.model.chars_per_token)
        for message in messages
    )
    before = state["budget"]
    budget = spent(
        before,
        began,
        tool_calls=before["tool_calls"] + len(reads),
        tokens_waiting=before["tokens_waiting"] + waiting,
    )
    await context.journal.budget(budget, context.model.name)
    update = {
        "messages": messages,
        "tool_calls": [record for _outcome, record in done],
        "facts": facts,
        "invalid_calls": invalid,
        "caveats": caveats,
        "budget": budget,
    }
    if any(count >= context.limits.runs.invalid_calls_max for count in invalid.values()):
        return Command(
            goto=names.STOP,
            update=update
            | {
                "stop_code": names.INVALID_CALLS,
                "stop_reason": (
                    "The assistant kept asking for something the server does not accept."
                ),
            },
        )
    return Command(goto=names.REASON, update=update)

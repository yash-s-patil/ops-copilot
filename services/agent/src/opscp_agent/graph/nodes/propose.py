"""propose: asks the server to record each change the model wants. Nothing is changed.

A write tool called without an approval only records the request and answers
"approval_required". This step never holds an approval, so it cannot do more than ask.
"""

import hashlib
import time
from datetime import datetime
from typing import Any
from uuid import UUID

from langgraph.runtime import Runtime
from langgraph.types import Command

from opscp_agent.events import types as events
from opscp_agent.graph import names
from opscp_agent.graph.context import RunContext
from opscp_agent.graph.nodes.gate import split
from opscp_agent.graph.state import ActionOutcome, PendingAction, RunState, outcome_of
from opscp_agent.graph.steps import PROPOSE, finished, spent, started, tool_message
from opscp_agent.guardrails.output import check_actions
from opscp_agent.guardrails.spotlight import spotlighted
from opscp_agent.llm.base import Finding, Message, ToolCall
from opscp_agent.mcp_client.tool_adapter import (
    APPROVAL_REQUIRED,
    APPROVAL_TOKEN,
    IDEMPOTENCY_KEY,
    CallRecord,
)
from opscp_agent.mcp_client.types import ToolOutcome
from opscp_agent.runs.store import RunAction

_KEY_HEX_CHARS = 40
DROPPED = "dropped"
REFUSED = "refused"


def idempotency_key(run_id: str, call_id: str) -> str:
    """One key for each change a run intends, the same however often the step is repeated."""
    digest = hashlib.sha256(f"{run_id}:{call_id}".encode()).hexdigest()
    return f"run-{digest[:_KEY_HEX_CHARS]}"


def _asked(call: ToolCall) -> ToolCall:
    """The call without the two arguments that are never the model's to give."""
    args = {
        name: value
        for name, value in call["args"].items()
        if name not in (IDEMPOTENCY_KEY, APPROVAL_TOKEN)
    }
    return ToolCall(id=call["id"], name=call["name"], args=args)


def _evidence(cited: list[str], records: list[CallRecord]) -> list[dict[str, Any]]:
    by_id = {record["provenance_id"]: record for record in records}
    return [
        {
            "provenance": cited_id,
            "tool": by_id[cited_id]["tool"],
            "summary": by_id[cited_id]["summary"],
        }
        for cited_id in cited
        if cited_id in by_id
    ]


def _pending(
    call: ToolCall, outcome: ToolOutcome, key: str, cited: list[str]
) -> PendingAction | None:
    data = outcome.data
    if outcome.status != "ok" or data is None or data.get("status") != APPROVAL_REQUIRED:
        return None
    return PendingAction(
        action_id=str(data.get("action_id")),
        call_id=call["id"],
        tool=call["name"],
        args=call["args"],
        args_hash=str(data.get("args_hash")),
        approval_kind=str(data.get("approval_kind")),
        summary=str(data.get("summary")),
        expires_at=str(data.get("expires_at")),
        idempotency_key=key,
        evidence=cited,
    )


async def _ask(
    state: RunState, context: RunContext, call: ToolCall, cited: list[str]
) -> tuple[ToolOutcome, CallRecord, PendingAction | None]:
    key = idempotency_key(state["run_id"], call["id"])
    await started(context, call, PROPOSE)
    outcome = await context.mcp.call(call["name"], call["args"] | {IDEMPOTENCY_KEY: key})
    record = await finished(context, call, PROPOSE, outcome)
    pending = _pending(call, outcome, key, cited)
    if pending is not None:
        await context.journal.action(
            RunAction(
                action_id=UUID(pending["action_id"]),
                run_id=context.journal.run_id,
                call_id=call["id"],
                tool=call["name"],
                idempotency_key=key,
                expires_at=datetime.fromisoformat(pending["expires_at"]),
            )
        )
        await context.journal.emit(
            events.APPROVAL_REQUIRED,
            f"approval.required:{pending['action_id']}",
            {
                "action_id": pending["action_id"],
                "call_id": call["id"],
                "tool": call["name"],
                "args": call["args"],
                "args_hash": pending["args_hash"],
                "approval_kind": pending["approval_kind"],
                "summary": pending["summary"],
                "evidence": _evidence(cited, state["tool_calls"]),
                "expires_at": pending["expires_at"],
            },
        )
    return outcome, record, pending


async def propose(state: RunState, runtime: Runtime[RunContext]) -> Command[str]:
    began = time.monotonic()
    context = runtime.context
    calls = [_asked(call) for call in split(state).writes]
    finding = None if state["finding"] is None else Finding.model_validate(state["finding"])
    kept, dropped = check_actions(
        calls,
        finding,
        state["tool_calls"],
        state["facts"],
        run_order_id=state["order_id"],
        goodwill=context.limits.goodwill,
    )
    messages: list[Message] = []
    outcomes: list[ActionOutcome] = []
    guardrails: list[dict[str, Any]] = []
    for call, triggered in dropped:
        guardrails.append(
            await context.journal.guardrail(triggered, f"{call['id']}:{triggered.rule}")
        )
        messages.append(
            Message(
                role="tool",
                content=spotlighted("agent", {"not_done": triggered.detail}),
                tool_call_id=call["id"],
            )
        )
        outcomes.append(outcome_of(call, DROPPED, detail=triggered.detail))
    cited_by = {
        action.call_id: list(action.evidence)
        for action in (finding.proposed_actions if finding else ())
    }
    pending: list[PendingAction] = []
    records: list[CallRecord] = []
    for call in kept:
        outcome, record, waiting = await _ask(state, context, call, cited_by.get(call["id"], []))
        records.append(record)
        messages.append(tool_message(call, outcome))
        if waiting is not None:
            pending.append(waiting)
            continue
        refusal = None if outcome.error is None else outcome.error.message
        outcomes.append(outcome_of(call, REFUSED, detail=refusal))
    before = state["budget"]
    budget = spent(before, began, tool_calls=before["tool_calls"] + len(kept))
    await context.journal.budget(budget, context.model.name)
    return Command(
        goto=names.AWAIT_APPROVAL if pending else names.REASON,
        update={
            "messages": messages,
            "tool_calls": records,
            "pending_actions": pending,
            "actions": outcomes,
            "guardrail_events": guardrails,
            "budget": budget,
        },
    )

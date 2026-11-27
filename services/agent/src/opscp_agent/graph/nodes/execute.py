"""execute: carries out what a person approved, and only that.

Each change is made by calling its write tool again with the same arguments, the same
key, and an approval the server issues to the requester for that one action.
"""

import time
from typing import Any
from uuid import UUID

from langgraph.runtime import Runtime
from langgraph.types import Command

from opscp_agent.graph import names
from opscp_agent.graph.context import RunContext
from opscp_agent.graph.state import ActionOutcome, PendingAction, RunState, call_of, outcome_of
from opscp_agent.graph.steps import (
    EXECUTE,
    call_once_more_if_passing,
    finished,
    spent,
    started,
)
from opscp_agent.guardrails.spotlight import spotlighted
from opscp_agent.llm.base import Message
from opscp_agent.mcp_client.tool_adapter import APPROVAL_TOKEN, IDEMPOTENCY_KEY, CallRecord
from opscp_agent.mcp_client.types import McpCallError, McpUnavailableError

EXECUTED = "executed"
FAILED = "failed"
UNKNOWN = "unknown"
NOT_REACHED = "Meridian's systems could not be reached, so this was not done."
UNCONFIRMED = (
    "Meridian's systems stopped answering while this was being done. "
    "Open the ticket to see whether it was."
)


async def carry_out(
    context: RunContext, action: PendingAction
) -> tuple[ActionOutcome, CallRecord | None]:
    """One approved change. A refusal is an outcome, not an error: the rest still run."""
    call = call_of(action)
    action_id = UUID(action["action_id"])
    try:
        approval = await context.mcp.approval_token(action_id)
    except McpCallError as refused:
        # No approval is given for a change already made, as when this step is repeated
        # after the process that began it was lost.
        if (await context.mcp.approval(action_id)).get("status") == EXECUTED:
            return _outcome(action, EXECUTED, None, None), None
        return _outcome(action, FAILED, refused.body.message, None), None
    except McpUnavailableError:
        return _outcome(action, FAILED, NOT_REACHED, None), None
    await started(context, call, EXECUTE)
    sent = action["args"] | {
        IDEMPOTENCY_KEY: action["idempotency_key"],
        APPROVAL_TOKEN: approval,
    }
    try:
        outcome = await call_once_more_if_passing(context, call, sent)
    except McpUnavailableError:
        # The request may have arrived. Saying it failed, or that it was made, would be a guess.
        return _outcome(action, UNKNOWN, UNCONFIRMED, None), None
    record = await finished(context, call, EXECUTE, outcome)
    data = outcome.data or {}
    if outcome.status == "ok" and data.get("status") == EXECUTED:
        return _outcome(action, EXECUTED, None, data.get("result")), record
    detail = outcome.error.message if outcome.error else "The change was refused."
    return _outcome(action, FAILED, detail, None), record


def _outcome(
    action: PendingAction, status: str, detail: str | None, result: dict[str, Any] | None
) -> ActionOutcome:
    return outcome_of(
        call_of(action), status, action_id=action["action_id"], detail=detail, result=result
    )


async def execute(state: RunState, runtime: Runtime[RunContext]) -> Command[str]:
    began = time.monotonic()
    context = runtime.context
    outcomes: list[ActionOutcome] = []
    records: list[CallRecord] = []
    for action in state["pending_actions"]:
        outcome, record = await carry_out(context, action)
        outcomes.append(outcome)
        if record is not None:
            records.append(record)
    done = [
        {"tool": outcome["tool"], "status": outcome["status"], "result": outcome["result"]}
        for outcome in outcomes
    ]
    before = state["budget"]
    budget = spent(before, began, tool_calls=before["tool_calls"] + len(records))
    await context.journal.budget(budget, context.model.name)
    return Command(
        goto=names.REPLY,
        update={
            "messages": [
                Message(role="user", content=spotlighted("approved_changes", {"changes": done}))
            ],
            "tool_calls": records,
            "pending_actions": [],
            "actions": outcomes,
            "budget": budget,
        },
    )

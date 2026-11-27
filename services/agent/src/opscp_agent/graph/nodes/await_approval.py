"""await_approval: the run stops here until people have decided. Its state is saved.

No approval can be given from inside the graph. Decisions are made through routes that sit
behind a person's click; each is noted as it passes through the agent and read here. What
is noted is only a note: the MCP server holds the decision and checks it again before
anything is changed.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt

from opscp_agent.events import types as events
from opscp_agent.graph import names
from opscp_agent.graph.context import RunContext
from opscp_agent.graph.state import ActionOutcome, PendingAction, RunState, call_of, outcome_of
from opscp_agent.guardrails.spotlight import spotlighted
from opscp_agent.llm.base import Message
from opscp_agent.runs.store import RunAction

APPROVED = "approved"
REJECTED = "rejected"
EXPIRED = "expired"
REJECTION = "The engineer rejected the proposed changes. What they gave as the reason follows."
_DECIDED = (APPROVED, REJECTED, EXPIRED, "executed", "failed")


def decision_in(view: dict[str, Any], now: datetime) -> str | None:
    """How the server says an action stands; None while a person may still decide."""
    status = view.get("status")
    if status in _DECIDED:
        return str(status)
    expires_at = datetime.fromisoformat(str(view.get("expires_at")))
    return EXPIRED if expires_at <= now else None


async def _decided(
    context: RunContext, action: PendingAction, noted: RunAction | None
) -> tuple[str, str | None, str | None]:
    """(decision, who decided, their reason) for one change the run is waiting on.

    A change with no decision noted has run out of time, or was decided somewhere that did
    not pass through the agent. Only then is the server asked.
    """
    if noted is not None and noted.decision is not None:
        return noted.decision, noted.decided_by, noted.decision_reason
    view = await context.mcp.approval(UUID(action["action_id"]))
    decision = decision_in(view, context.now())
    if decision is None:
        raise RuntimeError("A run was resumed while a decision was still open")
    return decision, view.get("approver_name"), view.get("decision_reason")


async def await_approval(state: RunState, runtime: Runtime[RunContext]) -> Command[str]:
    interrupt({"actions": [action["action_id"] for action in state["pending_actions"]]})
    context = runtime.context
    approved: list[PendingAction] = []
    outcomes: list[ActionOutcome] = []
    reasons: list[str] = []
    noted = await context.journal.actions()
    for action in state["pending_actions"]:
        decision, by, reason = await _decided(context, action, noted.get(action["action_id"]))
        await context.journal.emit(
            events.APPROVAL_RESOLVED,
            f"approval.resolved:{action['action_id']}",
            {
                "action_id": action["action_id"],
                "decision": decision,
                "approver": by,
                "approval_kind": action["approval_kind"],
            },
        )
        if decision == APPROVED:
            approved.append(action)
            continue
        if decision == REJECTED:
            reasons.append(reason or "No reason was given.")
        outcomes.append(
            outcome_of(call_of(action), decision, action_id=action["action_id"], detail=reason)
        )
    update: dict[str, Any] = {"pending_actions": approved, "actions": outcomes}
    if approved:
        return Command(goto=names.EXECUTE, update=update)
    if not reasons:
        return Command(
            goto=names.STOP,
            update=update
            | {
                "stop_code": names.NOT_APPROVED,
                "stop_reason": "Nobody approved the proposed changes in time, so nothing changed.",
            },
        )
    rejections = state["rejections"] + 1
    update["rejections"] = rejections
    if rejections >= context.limits.runs.rejections_max:
        return Command(
            goto=names.STOP,
            update=update
            | {
                "stop_code": names.REJECTED,
                "stop_reason": "The proposed changes were rejected again, so nothing changed.",
            },
        )
    # The reason may be a supervisor's words and not the run's owner's, so it is data too.
    told = f"{REJECTION}\n{spotlighted('rejection', {'reasons': reasons})}"
    return Command(
        goto=names.REASON, update=update | {"messages": [Message(role="user", content=told)]}
    )

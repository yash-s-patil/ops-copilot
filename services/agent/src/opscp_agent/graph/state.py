"""What a run remembers between steps. Everything here is saved at each checkpoint.

No token is kept in this state: tokens are held in memory by the code that makes the calls.
The canary is not kept here either; the system prompt is rebuilt for each model call.
"""

import operator
from typing import Annotated, Any, TypedDict

from opscp_agent.guardrails.budget import Budget
from opscp_agent.llm.base import Message, ToolCall
from opscp_agent.mcp_client.tool_adapter import CallRecord, Facts


class PendingAction(TypedDict):
    """A change the server has recorded and a person has yet to decide on."""

    action_id: str
    call_id: str
    tool: str
    args: dict[str, Any]
    args_hash: str
    approval_kind: str
    summary: str
    expires_at: str
    idempotency_key: str
    evidence: list[str]


class ActionOutcome(TypedDict):
    """How one requested change ended."""

    call_id: str
    tool: str
    status: str
    action_id: str | None
    escalates: bool
    detail: str | None
    result: dict[str, Any] | None


def call_of(action: PendingAction) -> ToolCall:
    return ToolCall(id=action["call_id"], name=action["tool"], args=action["args"])


def outcome_of(
    call: ToolCall,
    status: str,
    *,
    action_id: str | None = None,
    detail: str | None = None,
    result: dict[str, Any] | None = None,
) -> ActionOutcome:
    return ActionOutcome(
        call_id=call["id"],
        tool=call["name"],
        status=status,
        action_id=action_id,
        escalates=call["args"].get("escalate_to") is not None,
        detail=detail,
        result=result,
    )


class RunState(TypedDict):
    run_id: str
    user: dict[str, Any]
    ticket_id: int | None
    order_id: int | None
    message: str
    messages: Annotated[list[Message], operator.add]
    resources: list[dict[str, Any]]
    procedure: str
    tools: list[dict[str, Any]]
    turn: dict[str, Any] | None
    finding: dict[str, Any] | None
    tool_calls: Annotated[list[CallRecord], operator.add]
    facts: Facts
    pending_actions: list[PendingAction]
    actions: Annotated[list[ActionOutcome], operator.add]
    budget: Budget
    rejections: int
    invalid_calls: dict[str, int]
    caveats: Annotated[list[str], operator.add]
    guardrail_events: Annotated[list[dict[str, Any]], operator.add]
    status: str
    stop_reason: str | None
    stop_code: str | None
    result: dict[str, Any] | None

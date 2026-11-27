"""What several steps of a run share: calling a tool and telling the person about it."""

import time
from typing import Any

from opscp_agent.events import types as events
from opscp_agent.events.summaries import step_summary
from opscp_agent.graph.context import RunContext
from opscp_agent.guardrails.budget import Budget
from opscp_agent.guardrails.spotlight import HUMAN_ONLY_KEYS, spotlighted
from opscp_agent.jsonish import as_list, as_object
from opscp_agent.llm.base import Message, ToolCall
from opscp_agent.mcp_client.tool_adapter import (
    APPROVAL_TOKEN,
    IDEMPOTENCY_KEY,
    CallRecord,
    any_truncated,
    record_of,
)
from opscp_agent.mcp_client.types import Json, ToolOutcome
from opscp_common.redaction import redacted

_MS_PER_S = 1000
READ = "read"
PROPOSE = "propose"
EXECUTE = "execute"
_UNSENT = frozenset({IDEMPOTENCY_KEY, APPROVAL_TOKEN})


def spent(budget: Budget, started: float, **changed: int) -> Budget:
    """The budget with the time since `started` added, and any counts changed."""
    elapsed = budget["elapsed_ms"] + round((time.monotonic() - started) * _MS_PER_S)
    after = budget.copy()
    after.update(changed)  # pyright: ignore[reportCallIssue, reportArgumentType] -- the caller names Budget's own keys
    after["elapsed_ms"] = elapsed
    return after


def shown_args(call: ToolCall, context: RunContext) -> Json:
    """A call's arguments as an event may carry them: contact details hashed, text cut short."""
    args = {name: value for name, value in call["args"].items() if name not in _UNSENT}
    return redacted(args, args.keys(), context.limits.audit.text_chars)


def _previews(value: Any) -> list[str]:
    """Text the scanner withheld, for the person's eyes only."""
    found: list[str] = []
    for key, item in as_object(value).items():
        if key in HUMAN_ONLY_KEYS and isinstance(item, str):
            found.append(item)
        else:
            found += _previews(item)
    for item in as_list(value):
        found += _previews(item)
    return found


def tool_message(call: ToolCall, outcome: ToolOutcome) -> Message:
    """The result as the model reads it: marked as data, with nothing meant for the person."""
    if outcome.status == "ok" and outcome.data is not None:
        content = spotlighted(outcome.provenance_id or call["name"], outcome.data)
    else:
        body = None if outcome.error is None else outcome.error.model_dump(mode="json")
        content = spotlighted(call["name"], {"error": body})
    return Message(role="tool", content=content, tool_call_id=call["id"])


async def started(context: RunContext, call: ToolCall, phase: str) -> None:
    await context.journal.emit(
        events.TOOL_STARTED,
        f"tool.started:{call['id']}:{phase}",
        {
            "call_id": call["id"],
            "tool": call["name"],
            "phase": phase,
            "args": shown_args(call, context),
        },
    )


async def finished(
    context: RunContext, call: ToolCall, phase: str, outcome: ToolOutcome
) -> CallRecord:
    """Tells the person how a call ended and returns what the run keeps of it."""
    summary = step_summary(call["name"], outcome, context.limits.ui.step_summary_chars)
    record = record_of(call["id"], call["name"], phase, outcome, summary)
    if outcome.sign_in_refreshed:
        await context.journal.emit(
            events.AUTH_REFRESHED, f"auth.refreshed:{call['id']}:{phase}", {"call_id": call["id"]}
        )
    preview_chars = context.limits.ui.step_summary_chars
    await context.journal.emit(
        events.TOOL_FINISHED,
        f"tool.finished:{call['id']}:{phase}",
        {
            "call_id": call["id"],
            "tool": call["name"],
            "phase": phase,
            "status": outcome.status,
            "error_code": record["error_code"],
            "summary": summary,
            "duration_ms": outcome.duration_ms,
            "bytes": outcome.size_bytes,
            "truncated": any_truncated(outcome.data),
            "quarantined": record["quarantined"],
            "provenance": outcome.provenance_id,
            "withheld": [preview[:preview_chars] for preview in _previews(outcome.data)],
        },
    )
    return record


async def call_once_more_if_passing(
    context: RunContext, call: ToolCall, arguments: Json
) -> ToolOutcome:
    """A failure the server says may pass is tried once more. A write repeats its own key."""
    outcome = await context.mcp.call(call["name"], arguments)
    if outcome.error is not None and outcome.error.retryable:
        return await context.mcp.call(call["name"], arguments)
    return outcome

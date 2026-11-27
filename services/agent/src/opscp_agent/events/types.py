"""The events of a run, as they are stored and streamed."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

RUN_STARTED = "run.started"
CONTEXT_LOADED = "context.loaded"
MODEL_DELTA = "model.delta"
TOOL_STARTED = "tool.started"
TOOL_FINISHED = "tool.finished"
AUTH_REFRESHED = "auth.refreshed"
GUARDRAIL_TRIGGERED = "guardrail.triggered"
APPROVAL_REQUIRED = "approval.required"
APPROVAL_RESOLVED = "approval.resolved"
BUDGET_UPDATED = "budget.updated"
RUN_PAUSED = "run.paused"
RUN_STOPPED = "run.stopped"
RUN_COMPLETED = "run.completed"
RUN_FAILED = "run.failed"

EVENT_TYPES = (
    RUN_STARTED,
    CONTEXT_LOADED,
    MODEL_DELTA,
    TOOL_STARTED,
    TOOL_FINISHED,
    AUTH_REFRESHED,
    GUARDRAIL_TRIGGERED,
    APPROVAL_REQUIRED,
    APPROVAL_RESOLVED,
    BUDGET_UPDATED,
    RUN_PAUSED,
    RUN_STOPPED,
    RUN_COMPLETED,
    RUN_FAILED,
)
FINAL = frozenset({RUN_STOPPED, RUN_COMPLETED, RUN_FAILED})


@dataclass(frozen=True, slots=True, kw_only=True)
class StoredEvent:
    run_id: str
    seq: int
    type: str
    payload: dict[str, Any]
    created_at: datetime

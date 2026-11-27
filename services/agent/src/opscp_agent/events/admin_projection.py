"""What an administrator may see of a run's events: how it went, never what it was about.

Each event type has a list of the fields that are kept, and of a call's arguments only
numbers and short codes are kept. Anything not listed is dropped, so
a field added to an event later stays hidden until someone decides otherwise. Model text,
step summaries, withheld-text previews, approval evidence, reply drafts and the person's
message are never listed.
"""

import re
from collections.abc import Mapping
from typing import Any

from opscp_agent.events import types as t
from opscp_agent.events.types import StoredEvent
from opscp_agent.jsonish import as_list, as_object

_KEPT: Mapping[str, frozenset[str]] = {
    t.RUN_STARTED: frozenset({"model"}),
    t.CONTEXT_LOADED: frozenset({"tools", "resources"}),
    t.MODEL_DELTA: frozenset({"turn", "attempt"}),
    t.TOOL_STARTED: frozenset({"call_id", "tool", "args", "phase"}),
    t.TOOL_FINISHED: frozenset(
        {
            "call_id",
            "tool",
            "phase",
            "status",
            "error_code",
            "duration_ms",
            "bytes",
            "truncated",
            "quarantined",
            "provenance",
        }
    ),
    t.AUTH_REFRESHED: frozenset({"call_id"}),
    t.GUARDRAIL_TRIGGERED: frozenset({"layer", "rule", "action", "severity"}),
    t.APPROVAL_REQUIRED: frozenset(
        {"action_id", "tool", "args_hash", "approval_kind", "expires_at"}
    ),
    t.APPROVAL_RESOLVED: frozenset({"action_id", "decision", "approval_kind"}),
    t.BUDGET_UPDATED: frozenset(
        {
            "tokens_in",
            "tokens_out",
            "tokens_cached",
            "estimated_cost_inr",
            "tool_calls",
            "model_turns",
            "elapsed_ms",
        }
    ),
    t.RUN_PAUSED: frozenset({"code"}),
    t.RUN_STOPPED: frozenset({"code"}),
    t.RUN_COMPLETED: frozenset({"escalated", "actions"}),
    t.RUN_FAILED: frozenset({"code"}),
}
_RESOURCE_FIELDS = frozenset({"uri", "version"})
_ACTION_FIELDS = frozenset({"tool", "status", "action_id"})
# A code, a name of ours or a hashed contact detail: never a sentence.
_CODE = re.compile(r"[A-Za-z0-9_.:<>-]{1,40}")


def _listed(items: object, fields: frozenset[str]) -> list[dict[str, Any]]:
    return [
        {name: item[name] for name in fields if name in item}
        for item in map(as_object, as_list(items))
        if item
    ]


def _plain(value: object) -> object:
    """A number or a short code as it is. Anything a person may have typed, by its kind."""
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, str) and _CODE.fullmatch(value):
        return value
    return f"<{type(value).__name__}>"


def _args(args: object) -> dict[str, object]:
    """Arguments without free text, which can name a customer however it was redacted."""
    shown: dict[str, object] = {}
    for name, value in as_object(args).items():
        nested = as_object(value)
        shown[name] = (
            {key: _plain(item) for key, item in nested.items()} if nested else _plain(value)
        )
    return shown


def admin_view(event: StoredEvent) -> dict[str, Any]:
    """The event as an administrator sees it. An unknown type shows nothing but its name."""
    kept = _KEPT.get(event.type, frozenset())
    payload = {name: value for name, value in event.payload.items() if name in kept}
    if "args" in payload:
        payload["args"] = _args(payload["args"])
    for chosen_by_the_model in ("tool", "call_id"):
        if chosen_by_the_model in payload:
            payload[chosen_by_the_model] = _plain(payload[chosen_by_the_model])
    if "resources" in payload:
        payload["resources"] = _listed(payload["resources"], _RESOURCE_FIELDS)
    if "actions" in payload:
        payload["actions"] = _listed(payload["actions"], _ACTION_FIELDS)
    return {
        "seq": event.seq,
        "type": event.type,
        "at": event.created_at.isoformat(),
        "payload": payload,
    }

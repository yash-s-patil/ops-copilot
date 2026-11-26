import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from opscp_common.limits import load_limits

LIMITS_FILE = Path(__file__).resolve().parents[3] / "config" / "limits.yaml"
SECONDS_PER_MINUTE = 60

APPENDIX_A: dict[str, dict[str, Any]] = {
    "events": {"recent": 10},
    "event": {"summary_chars": 200},
    "tickets": {"page_size_default": 10, "page_size_max": 25},
    "ticket": {"summary_chars": 160, "note_write_chars": 1000},
    "notes": {"default": 5, "max": 10},
    "note": {"chars": 600},
    "runbooks": {"top_k_default": 3, "top_k_max": 5},
    "runbook": {"chunk_chars": 1200},
    "policy": {"section_chars": 2000},
    "search": {"query_chars_min": 3, "query_chars_max": 200},
    "refund": {"reason_chars": 300},
    "tool": {"result_bytes_max": 16384},
    "tool_timeout": {
        "lookup_customer_s": 3,
        "get_order_s": 3,
        "list_tickets_s": 3,
        "get_ticket_s": 3,
        "search_runbooks_s": 6,
        "get_policy_document_s": 5,
        "update_ticket_s": 5,
        "issue_refund_s": 5,
    },
    "user": {"message_chars": 2000},
    "rate": {"calls_per_minute": 60, "burst": 20},
    "timeout": {
        "postgres_s": 3,
        "blob_s": 5,
        "search_s": 5,
        "content_safety_s": 2,
        "keyvault_s": 3,
        "embeddings_s": 5,
    },
    "retry": {"max": 2, "base_ms": 100},
    "breaker": {"failures": 5, "window_s": 30, "half_open_after_s": 20},
    "bulkhead": {"per_backend": 20},
    "approval": {
        "token_ttl_s": 120,
        "pending_ttl_self_s": 900,
        "pending_ttl_supervisor_s": 86400,
        "self_limit_minor": 500000,
    },
    "goodwill": {
        "premium": {"min_delay_hours": 72, "max_percent": 15},
        "standard": {"min_delay_hours": 120, "max_percent": 10},
    },
    "budget": {"tool_calls": 15, "model_turns": 8, "tokens": 60000, "wall_clock_s": 120},
    "token": {"refresh_before_expiry_s": 300, "clock_skew_s": 60},
    "health": {"cache_s": 15},
    "retention": {"copilot_days": 30},
    "inbox": {"page_size": 25},
    "ui": {
        "inbox_refresh_s": 15,
        "refund_poll_s": 3,
        "refund_poll_max_duration_s": 120,
        "step_summary_chars": 300,
    },
    "payments": {
        "poll_interval_s": 2,
        "claim_batch": 10,
        "max_attempts": 5,
        "backoff_base_s": 1,
        "backoff_max_s": 30,
        "http_timeout_s": 5,
    },
    "sim": {"settle_delay_s": 3},
}


def _write(tmp_path: Path, content: object) -> Path:
    path = tmp_path / "limits.yaml"
    # JSON is a subset of YAML.
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


def _with(group: str, key: str, value: object) -> dict[str, dict[str, Any]]:
    changed = copy.deepcopy(APPENDIX_A)
    changed[group][key] = value
    return changed


def test_repository_limits_match_appendix_a() -> None:
    assert load_limits(LIMITS_FILE).model_dump() == APPENDIX_A


def test_limits_cannot_be_changed_after_loading() -> None:
    limits = load_limits(LIMITS_FILE)

    with pytest.raises(ValidationError):
        limits.rate.burst = 1000


def test_screen_reads_and_one_run_fit_inside_the_rate_limit() -> None:
    limits = load_limits(LIMITS_FILE)

    inbox_refreshes = SECONDS_PER_MINUTE // limits.ui.inbox_refresh_s
    refund_polls = SECONDS_PER_MINUTE // limits.ui.refund_poll_s
    calls_per_minute = inbox_refreshes + refund_polls + limits.budget.tool_calls

    assert calls_per_minute < limits.rate.calls_per_minute


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        load_limits(_write(tmp_path, _with("rate", "unlimited", 1)))


def test_missing_group_is_rejected(tmp_path: Path) -> None:
    incomplete = copy.deepcopy(APPENDIX_A)
    del incomplete["budget"]

    with pytest.raises(ValidationError, match="budget"):
        load_limits(_write(tmp_path, incomplete))


@pytest.mark.parametrize("value", [0, -1, "10", 2.5, None])
def test_value_that_is_not_a_positive_integer_is_rejected(tmp_path: Path, value: object) -> None:
    with pytest.raises(ValidationError):
        load_limits(_write(tmp_path, _with("events", "recent", value)))


@pytest.mark.parametrize(
    ("group", "key", "value"),
    [
        ("tickets", "page_size_default", 26),
        ("notes", "default", 11),
        ("runbooks", "top_k_default", 6),
        ("search", "query_chars_min", 201),
        ("payments", "backoff_base_s", 31),
    ],
)
def test_lower_bound_above_upper_bound_is_rejected(
    tmp_path: Path, group: str, key: str, value: int
) -> None:
    with pytest.raises(ValidationError, match="must not exceed"):
        load_limits(_write(tmp_path, _with(group, key, value)))


def test_percentage_above_one_hundred_is_rejected(tmp_path: Path) -> None:
    changed = copy.deepcopy(APPENDIX_A)
    changed["goodwill"]["premium"]["max_percent"] = 101

    with pytest.raises(ValidationError):
        load_limits(_write(tmp_path, changed))


def test_empty_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "limits.yaml"
    path.write_text("", encoding="utf-8")

    with pytest.raises(ValidationError):
        load_limits(path)


def test_missing_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_limits(tmp_path / "absent.yaml")

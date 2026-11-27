"""Typed, validated access to config/limits.yaml."""

from pathlib import Path
from typing import Annotated, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

Percent = Annotated[int, Field(gt=0, le=100)]


class _Group(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _require_ordered(lower: int, upper: int, lower_name: str, upper_name: str) -> None:
    if lower > upper:
        raise ValueError(f"{lower_name} ({lower}) must not exceed {upper_name} ({upper})")


class EventsLimits(_Group):
    recent: PositiveInt


class EventLimits(_Group):
    summary_chars: PositiveInt
    detail_chars: PositiveInt


class StuckLimits(_Group):
    paid_hours: PositiveInt
    packed_hours: PositiveInt
    shipped_no_scan_hours: PositiveInt


class TicketsLimits(_Group):
    page_size_default: PositiveInt
    page_size_max: PositiveInt
    count_max: PositiveInt

    @model_validator(mode="after")
    def _default_within_max(self) -> Self:
        _require_ordered(
            self.page_size_default, self.page_size_max, "page_size_default", "page_size_max"
        )
        return self


class TicketLimits(_Group):
    summary_chars: PositiveInt
    summary_bytes: PositiveInt
    note_write_chars: PositiveInt


class NotesLimits(_Group):
    default: PositiveInt
    max: PositiveInt

    @model_validator(mode="after")
    def _default_within_max(self) -> Self:
        _require_ordered(self.default, self.max, "default", "max")
        return self


class NoteLimits(_Group):
    chars: PositiveInt
    bytes: PositiveInt


class RunbooksLimits(_Group):
    top_k_default: PositiveInt
    top_k_max: PositiveInt
    index_max: PositiveInt

    @model_validator(mode="after")
    def _default_within_max(self) -> Self:
        _require_ordered(self.top_k_default, self.top_k_max, "top_k_default", "top_k_max")
        return self


class RunbookLimits(_Group):
    chunk_chars: PositiveInt
    chunk_bytes: PositiveInt


class PolicyLimits(_Group):
    section_chars: PositiveInt
    document_bytes: PositiveInt


class SearchLimits(_Group):
    query_chars_min: PositiveInt
    query_chars_max: PositiveInt

    @model_validator(mode="after")
    def _min_within_max(self) -> Self:
        _require_ordered(
            self.query_chars_min, self.query_chars_max, "query_chars_min", "query_chars_max"
        )
        return self


class RefundLimits(_Group):
    reason_chars: PositiveInt
    reason_bytes: PositiveInt


class NameLimits(_Group):
    chars: PositiveInt
    bytes: PositiveInt


class CursorLimits(_Group):
    chars: PositiveInt


class ToolLimits(_Group):
    result_bytes_max: PositiveInt


class ToolTimeoutLimits(_Group):
    lookup_customer_s: PositiveInt
    get_order_s: PositiveInt
    list_tickets_s: PositiveInt
    get_ticket_s: PositiveInt
    search_runbooks_s: PositiveInt
    get_policy_document_s: PositiveInt
    update_ticket_s: PositiveInt
    issue_refund_s: PositiveInt


class UserLimits(_Group):
    message_chars: PositiveInt


class RateLimits(_Group):
    calls_per_minute: PositiveInt
    burst: PositiveInt


class AuditLimits(_Group):
    text_chars: PositiveInt


class TimeoutLimits(_Group):
    postgres_s: PositiveInt
    blob_s: PositiveInt
    search_s: PositiveInt
    content_safety_s: PositiveInt
    keyvault_s: PositiveInt
    embeddings_s: PositiveInt


class RetryLimits(_Group):
    max: PositiveInt
    base_ms: PositiveInt


class BreakerLimits(_Group):
    failures: PositiveInt
    window_s: PositiveInt
    half_open_after_s: PositiveInt


class BulkheadLimits(_Group):
    per_backend: PositiveInt


class ApprovalLimits(_Group):
    token_ttl_s: PositiveInt
    pending_ttl_self_s: PositiveInt
    pending_ttl_supervisor_s: PositiveInt
    approved_ttl_s: PositiveInt
    decision_reason_chars: PositiveInt
    list_max: PositiveInt
    route_timeout_s: PositiveInt
    body_bytes_max: PositiveInt
    self_limit_minor: PositiveInt


class GoodwillRule(_Group):
    min_delay_hours: PositiveInt
    max_percent: Percent


class GoodwillLimits(_Group):
    premium: GoodwillRule
    standard: GoodwillRule


class BudgetLimits(_Group):
    tool_calls: PositiveInt
    model_turns: PositiveInt
    tokens: PositiveInt
    wall_clock_s: PositiveInt


class TokenLimits(_Group):
    refresh_before_expiry_s: PositiveInt
    clock_skew_s: PositiveInt


class HealthLimits(_Group):
    cache_s: PositiveInt


class RetentionLimits(_Group):
    copilot_days: PositiveInt


class InboxLimits(_Group):
    page_size: PositiveInt


class UiLimits(_Group):
    inbox_refresh_s: PositiveInt
    refund_poll_s: PositiveInt
    refund_poll_max_duration_s: PositiveInt
    step_summary_chars: PositiveInt


class PaymentsLimits(_Group):
    poll_interval_s: PositiveInt
    claim_batch: PositiveInt
    max_attempts: PositiveInt
    backoff_base_s: PositiveInt
    backoff_max_s: PositiveInt
    http_timeout_s: PositiveInt

    @model_validator(mode="after")
    def _base_within_max(self) -> Self:
        _require_ordered(self.backoff_base_s, self.backoff_max_s, "backoff_base_s", "backoff_max_s")
        return self


class SimLimits(_Group):
    settle_delay_s: PositiveInt


class Limits(_Group):
    events: EventsLimits
    event: EventLimits
    stuck: StuckLimits
    tickets: TicketsLimits
    ticket: TicketLimits
    notes: NotesLimits
    note: NoteLimits
    runbooks: RunbooksLimits
    runbook: RunbookLimits
    policy: PolicyLimits
    search: SearchLimits
    cursor: CursorLimits
    refund: RefundLimits
    name: NameLimits
    tool: ToolLimits
    tool_timeout: ToolTimeoutLimits
    user: UserLimits
    rate: RateLimits
    audit: AuditLimits
    timeout: TimeoutLimits
    retry: RetryLimits
    breaker: BreakerLimits
    bulkhead: BulkheadLimits
    approval: ApprovalLimits
    goodwill: GoodwillLimits
    budget: BudgetLimits
    token: TokenLimits
    health: HealthLimits
    retention: RetentionLimits
    inbox: InboxLimits
    ui: UiLimits
    payments: PaymentsLimits
    sim: SimLimits


def load_limits(path: Path) -> Limits:
    with path.open(encoding="utf-8") as handle:
        return Limits.model_validate(yaml.safe_load(handle))

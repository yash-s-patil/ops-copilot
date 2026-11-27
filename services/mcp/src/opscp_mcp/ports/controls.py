"""The record of what was asked of the server, and the count of how often."""

from typing import Literal, Protocol
from uuid import UUID

from opscp_common.hashing import JsonValue
from opscp_mcp.ports.base import Record

type ActorKind = Literal["user", "synthetic", "service"]
type AuditAction = Literal["call", "deny", "propose"]
type AuditOutcome = Literal["ok", "denied", "error"]


class AuditEntry(Record):
    trace_id: str | None = None
    run_id: str | None = None
    actor_oid: UUID
    actor_roles: tuple[str, ...]
    actor_kind: ActorKind
    target: str
    action: AuditAction
    args_redacted: dict[str, JsonValue]
    outcome: AuditOutcome
    error_code: str | None = None
    result_hash: str | None = None
    result_bytes: int | None = None
    duration_ms: int


class AuditTrail(Protocol):
    async def record(self, entry: AuditEntry) -> int:
        """Appends the entry and returns its number. Raising means it was not recorded."""
        ...


class Allowance(Record):
    allowed: bool
    retry_after_s: int


class RateLimiter(Protocol):
    async def take(self, user_oid: UUID) -> Allowance:
        """Counts one call against the user, unless they have none left."""
        ...

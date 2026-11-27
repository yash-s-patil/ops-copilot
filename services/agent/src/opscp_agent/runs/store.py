"""What the agent keeps about its runs, behind one interface with two implementations."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Protocol
from uuid import UUID

from opscp_agent.auth.verifier import Person
from opscp_agent.events.types import StoredEvent
from opscp_agent.guardrails.output import Triggered
from opscp_agent.llm.base import Usage

type RunStatus = Literal[
    "queued",
    "running",
    "awaiting_approval",
    "reauth_required",
    "completed",
    "stopped",
    "failed",
    "cancelled",
]
type Decision = Literal["approved", "rejected"]
type Settled = Literal["awaiting_approval", "reauth_required", "completed", "stopped", "failed"]

RESUMABLE: tuple[RunStatus, ...] = ("queued", "running", "reauth_required")
UNFINISHED: tuple[RunStatus, ...] = ("queued", "running", "awaiting_approval", "reauth_required")
AWAITING_APPROVAL: RunStatus = "awaiting_approval"
CANCELLED: RunStatus = "cancelled"


@dataclass(frozen=True, slots=True, kw_only=True)
class Run:
    id: UUID
    person: Person
    ticket_id: int | None
    order_id: int | None
    status: RunStatus
    message: str
    model: str
    tokens_in: int
    tokens_out: int
    tokens_cached: int
    estimated_cost_inr: Decimal | None
    tool_calls: int
    model_turns: int
    elapsed_ms: int
    result: dict[str, Any] | None
    stop_reason: str | None
    created_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True, slots=True, kw_only=True)
class NewRun:
    id: UUID
    person: Person
    ticket_id: int | None
    order_id: int | None
    message: str
    model: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RunAction:
    """A change one run asked for, the key it was asked under, and what was decided.

    The decision is the agent's note of what passed through it. The MCP server holds the
    decision itself and checks it again before anything is changed.
    """

    action_id: UUID
    run_id: UUID
    call_id: str
    tool: str
    idempotency_key: str
    expires_at: datetime
    decision: Decision | None = None
    decided_by: str | None = None
    decision_reason: str | None = None

    def is_open(self, now: datetime) -> bool:
        """Whether a person may still decide: nothing is noted and its time has not run out."""
        return self.decision is None and self.expires_at > now


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelCall:
    node: str
    model: str
    usage: Usage
    latency_ms: int
    cost_inr: Decimal | None


@dataclass(frozen=True, slots=True, kw_only=True)
class Progress:
    tool_calls: int
    model_turns: int
    elapsed_ms: int


@dataclass(frozen=True, slots=True, kw_only=True)
class Outcome:
    status: Settled
    result: dict[str, Any] | None = None
    stop_reason: str | None = None


class RunStore(Protocol):
    async def create(self, run: NewRun) -> None: ...

    async def get(self, run_id: UUID) -> Run | None: ...

    async def recent_for(self, user_oid: UUID, *, ask_only: bool, limit: int) -> list[Run]: ...

    async def on_ticket(self, ticket_id: int, limit: int) -> list[Run]: ...

    async def recent(self, limit: int) -> list[Run]: ...

    async def latest_id(self) -> UUID | None: ...

    async def claim(
        self, run_id: UUID, owner: UUID, lease_s: int, held: tuple[RunStatus, ...]
    ) -> int | None:
        """Takes the run for this process if it is in one of `held` and nobody holds it.

        The run becomes `running`. Returns which attempt at the run this is, or None.
        """
        ...

    async def heartbeat(self, run_id: UUID, owner: UUID, lease_s: int) -> bool:
        """Keeps the lease. False when the run is no longer this process's to work on."""
        ...

    async def settle(self, run_id: UUID, owner: UUID, outcome: Outcome) -> bool:
        """Gives the run up in its new status. False when it was no longer held."""
        ...

    async def cancel(self, run_id: UUID, user_oid: UUID) -> bool:
        """Ends an unfinished run of this person's. False when there was none to end."""
        ...

    async def record_model_call(self, run_id: UUID, call: ModelCall) -> None: ...

    async def record_progress(self, run_id: UUID, progress: Progress) -> None: ...

    async def record_guardrail(self, run_id: UUID, triggered: Triggered) -> None: ...

    async def add_action(self, action: RunAction) -> None: ...

    async def note_decision(
        self, action_id: UUID, decision: Decision, by: str, reason: str | None
    ) -> None:
        """Notes the first decision on a change. A later one does not replace it."""
        ...

    async def action(self, action_id: UUID) -> RunAction | None: ...

    async def actions_of(self, run_id: UUID) -> list[RunAction]: ...

    async def append_event(
        self, run_id: UUID, kind: str, key: str, payload: dict[str, Any]
    ) -> int | None:
        """Stores the event and returns its number, or None when this key was stored before."""
        ...

    async def events_after(self, run_id: UUID, seq: int, limit: int) -> list[StoredEvent]: ...

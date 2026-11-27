"""The run store in memory, with the same rules as the one in PostgreSQL."""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from opscp_agent.events.types import StoredEvent
from opscp_agent.guardrails.output import Triggered
from opscp_agent.runs.store import (
    UNFINISHED,
    Decision,
    ModelCall,
    NewRun,
    Outcome,
    Progress,
    Run,
    RunAction,
    RunStatus,
)

_FINISHED = ("completed", "stopped", "failed")


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class _Lease:
    owner: UUID
    until: datetime


@dataclass(slots=True)
class InMemoryRunStore:
    clock: Callable[[], datetime] = _now
    runs: dict[UUID, Run] = field(default_factory=dict[UUID, Run])
    events: dict[UUID, list[tuple[str, StoredEvent]]] = field(
        default_factory=dict[UUID, list[tuple[str, StoredEvent]]]
    )
    actions: dict[UUID, RunAction] = field(default_factory=dict[UUID, RunAction])
    model_calls: list[tuple[UUID, ModelCall]] = field(default_factory=list[tuple[UUID, ModelCall]])
    guardrails: list[tuple[UUID, Triggered]] = field(default_factory=list[tuple[UUID, Triggered]])
    leases: dict[UUID, _Lease] = field(default_factory=dict[UUID, _Lease])
    attempts: dict[UUID, int] = field(default_factory=dict[UUID, int])

    async def create(self, run: NewRun) -> None:
        self.runs[run.id] = Run(
            id=run.id,
            person=run.person,
            ticket_id=run.ticket_id,
            order_id=run.order_id,
            status="queued",
            message=run.message,
            model=run.model,
            tokens_in=0,
            tokens_out=0,
            tokens_cached=0,
            estimated_cost_inr=None,
            tool_calls=0,
            model_turns=0,
            elapsed_ms=0,
            result=None,
            stop_reason=None,
            created_at=self.clock() + timedelta(microseconds=len(self.runs)),
            finished_at=None,
        )
        self.events[run.id] = []

    async def get(self, run_id: UUID) -> Run | None:
        return self.runs.get(run_id)

    def _newest(self) -> list[Run]:
        return sorted(self.runs.values(), key=lambda run: run.created_at, reverse=True)

    async def recent_for(self, user_oid: UUID, *, ask_only: bool, limit: int) -> list[Run]:
        mine = [run for run in self._newest() if run.person.oid == user_oid]
        return [run for run in mine if not ask_only or run.ticket_id is None][:limit]

    async def on_ticket(self, ticket_id: int, limit: int) -> list[Run]:
        return [run for run in self._newest() if run.ticket_id == ticket_id][:limit]

    async def recent(self, limit: int) -> list[Run]:
        return self._newest()[:limit]

    async def latest_id(self) -> UUID | None:
        newest = self._newest()
        return newest[0].id if newest else None

    async def claim(
        self, run_id: UUID, owner: UUID, lease_s: int, held: tuple[RunStatus, ...]
    ) -> int | None:
        run = self.runs.get(run_id)
        lease = self.leases.get(run_id)
        if run is None or run.status not in held or (lease and lease.until >= self.clock()):
            return None
        self.runs[run_id] = replace(run, status="running")
        self.leases[run_id] = _Lease(owner, self.clock() + timedelta(seconds=lease_s))
        self.attempts[run_id] = self.attempts.get(run_id, 0) + 1
        return self.attempts[run_id]

    def _holds(self, run_id: UUID, owner: UUID) -> bool:
        lease = self.leases.get(run_id)
        run = self.runs.get(run_id)
        return bool(lease and run and lease.owner == owner and run.status == "running")

    async def heartbeat(self, run_id: UUID, owner: UUID, lease_s: int) -> bool:
        if not self._holds(run_id, owner):
            return False
        self.leases[run_id] = _Lease(owner, self.clock() + timedelta(seconds=lease_s))
        return True

    async def settle(self, run_id: UUID, owner: UUID, outcome: Outcome) -> bool:
        if not self._holds(run_id, owner):
            return False
        run = self.runs[run_id]
        self.runs[run_id] = replace(
            run,
            status=outcome.status,
            result=run.result if outcome.result is None else outcome.result,
            stop_reason=outcome.stop_reason,
            finished_at=self.clock() if outcome.status in _FINISHED else None,
        )
        del self.leases[run_id]
        return True

    async def cancel(self, run_id: UUID, user_oid: UUID) -> bool:
        run = self.runs.get(run_id)
        if run is None or run.person.oid != user_oid or run.status not in UNFINISHED:
            return False
        self.runs[run_id] = replace(run, status="cancelled", finished_at=self.clock())
        self.leases.pop(run_id, None)
        return True

    async def record_model_call(self, run_id: UUID, call: ModelCall) -> None:
        self.model_calls.append((run_id, call))
        run = self.runs[run_id]
        cost = run.estimated_cost_inr
        if call.cost_inr is not None:
            cost = (cost or Decimal(0)) + call.cost_inr
        self.runs[run_id] = replace(
            run,
            tokens_in=run.tokens_in + call.usage.tokens_in,
            tokens_out=run.tokens_out + call.usage.tokens_out,
            tokens_cached=run.tokens_cached + call.usage.tokens_cached,
            estimated_cost_inr=cost,
        )

    async def record_progress(self, run_id: UUID, progress: Progress) -> None:
        self.runs[run_id] = replace(
            self.runs[run_id],
            tool_calls=progress.tool_calls,
            model_turns=progress.model_turns,
            elapsed_ms=progress.elapsed_ms,
        )

    async def record_guardrail(self, run_id: UUID, triggered: Triggered) -> None:
        self.guardrails.append((run_id, triggered))

    async def add_action(self, action: RunAction) -> None:
        self.actions.setdefault(UUID(str(action.action_id)), action)

    async def note_decision(
        self, action_id: UUID, decision: Decision, by: str, reason: str | None
    ) -> None:
        noted = self.actions.get(action_id)
        if noted is not None and noted.decision is None:
            self.actions[action_id] = replace(
                noted, decision=decision, decided_by=by, decision_reason=reason
            )

    async def action(self, action_id: UUID) -> RunAction | None:
        return self.actions.get(action_id)

    async def actions_of(self, run_id: UUID) -> list[RunAction]:
        return [action for action in self.actions.values() if action.run_id == run_id]

    async def append_event(
        self, run_id: UUID, kind: str, key: str, payload: dict[str, Any]
    ) -> int | None:
        stored = self.events.get(run_id)
        if stored is None or any(known == key for known, _event in stored):
            return None
        seq = len(stored) + 1
        stored.append(
            (
                key,
                StoredEvent(
                    run_id=str(run_id), seq=seq, type=kind, payload=payload, created_at=self.clock()
                ),
            )
        )
        return seq

    async def events_after(self, run_id: UUID, seq: int, limit: int) -> list[StoredEvent]:
        stored = self.events.get(run_id, [])
        return [event for _key, event in stored if event.seq > seq][:limit]

    def kinds(self, run_id: UUID) -> list[str]:
        return [event.type for _key, event in self.events[run_id]]

    def payloads(self, run_id: UUID, kind: str) -> list[dict[str, Any]]:
        return [event.payload for _key, event in self.events[run_id] if event.type == kind]

"""The run store in the agent's own database, `copilot`.

It holds what runs did and found, which includes what their owners were allowed to read.
The business data itself is in another database, which the agent has no role in.
"""

import json
from typing import Any
from uuid import UUID

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncEngine

from opscp_agent.auth.verifier import Person
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
_RUN_COLUMNS = (
    "id, user_oid, user_name, roles, region, ticket_id, order_id, status, message, model, "
    "tokens_in, tokens_out, tokens_cached, estimated_cost_inr, tool_calls, model_turns, "
    "elapsed_ms, result, stop_reason, created_at, finished_at"
)

_CREATE = text(
    """
    INSERT INTO copilot.runs
        (id, user_oid, user_name, roles, region, ticket_id, order_id, message, model)
    VALUES
        (:id, :user_oid, :user_name, :roles, :region, :ticket_id, :order_id, :message, :model)
    """
)
_GET = text(f"SELECT {_RUN_COLUMNS} FROM copilot.runs WHERE id = :id")  # noqa: S608 -- fixed column list
_RECENT_FOR = text(
    f"""
    SELECT {_RUN_COLUMNS} FROM copilot.runs
    WHERE user_oid = :user_oid AND (NOT :ask_only OR ticket_id IS NULL)
    ORDER BY created_at DESC, id LIMIT :limit
    """  # noqa: S608 -- fixed column list
)
_ON_TICKET = text(
    f"""
    SELECT {_RUN_COLUMNS} FROM copilot.runs
    WHERE ticket_id = :ticket_id ORDER BY created_at DESC, id LIMIT :limit
    """  # noqa: S608 -- fixed column list
)
_RECENT = text(
    f"SELECT {_RUN_COLUMNS} FROM copilot.runs ORDER BY created_at DESC, id LIMIT :limit"  # noqa: S608 -- fixed column list
)
_LATEST = text("SELECT id FROM copilot.runs ORDER BY created_at DESC, id LIMIT 1")
_CLAIM = text(
    """
    UPDATE copilot.runs
    SET status = 'running', lease_owner = :owner,
        lease_until = now() + make_interval(secs => :lease_s), attempt = attempt + 1
    WHERE id = :id AND status = ANY(:held)
      AND (lease_until IS NULL OR lease_until < now())
    RETURNING attempt
    """
)
_HEARTBEAT = text(
    """
    UPDATE copilot.runs SET lease_until = now() + make_interval(secs => :lease_s)
    WHERE id = :id AND lease_owner = :owner AND status = 'running'
    RETURNING id
    """
)
_SETTLE = text(
    """
    UPDATE copilot.runs
    SET status = :status, result = COALESCE(CAST(:result AS jsonb), result),
        stop_reason = :stop_reason, lease_owner = NULL, lease_until = NULL,
        finished_at = CASE WHEN :status = ANY(:finished) THEN now() END
    WHERE id = :id AND lease_owner = :owner AND status = 'running'
    RETURNING id
    """
)
_CANCEL = text(
    """
    UPDATE copilot.runs
    SET status = 'cancelled', lease_owner = NULL, lease_until = NULL, finished_at = now()
    WHERE id = :id AND user_oid = :user_oid AND status = ANY(:unfinished)
    RETURNING id
    """
)
_ADD_MODEL_CALL = text(
    """
    INSERT INTO copilot.llm_calls (run_id, node, model, tokens_in, tokens_out, tokens_cached,
        latency_ms)
    VALUES (:run_id, :node, :model, :tokens_in, :tokens_out, :tokens_cached, :latency_ms)
    """
)
_ADD_USAGE = text(
    """
    UPDATE copilot.runs
    SET tokens_in = tokens_in + :tokens_in, tokens_out = tokens_out + :tokens_out,
        tokens_cached = tokens_cached + :tokens_cached,
        estimated_cost_inr = CASE WHEN CAST(:cost AS numeric) IS NULL THEN estimated_cost_inr
            ELSE COALESCE(estimated_cost_inr, 0) + CAST(:cost AS numeric) END
    WHERE id = :run_id
    """
)
_PROGRESS = text(
    """
    UPDATE copilot.runs
    SET tool_calls = :tool_calls, model_turns = :model_turns, elapsed_ms = :elapsed_ms
    WHERE id = :run_id
    """
)
_ADD_GUARDRAIL = text(
    """
    INSERT INTO copilot.guardrail_events (run_id, layer, rule, severity, action, detail)
    VALUES (:run_id, :layer, :rule, :severity, :action, :detail)
    """
)
_ADD_ACTION = text(
    """
    INSERT INTO copilot.run_actions
        (action_id, run_id, call_id, tool, idempotency_key, expires_at)
    VALUES (:action_id, :run_id, :call_id, :tool, :idempotency_key, :expires_at)
    ON CONFLICT (action_id) DO NOTHING
    """
)
_NOTE_DECISION = text(
    """
    UPDATE copilot.run_actions
    SET decision = :decision, decided_by = :by, decision_reason = :reason
    WHERE action_id = :action_id AND decision IS NULL
    """
)
_ACTION_COLUMNS = (
    "action_id, run_id, call_id, tool, idempotency_key, expires_at, decision, decided_by, "
    "decision_reason"
)
_ACTION = text(f"SELECT {_ACTION_COLUMNS} FROM copilot.run_actions WHERE action_id = :action_id")  # noqa: S608 -- fixed column list
_ACTIONS_OF = text(
    f"SELECT {_ACTION_COLUMNS} FROM copilot.run_actions WHERE run_id = :run_id ORDER BY created_at, call_id"  # noqa: S608, E501 -- fixed column list
)
# The run's row is locked while its next event is numbered, so numbers have no gaps and a
# key stored once is not stored again.
_LOCK_RUN = text("SELECT last_seq FROM copilot.runs WHERE id = :run_id FOR UPDATE")
_HAS_KEY = text("SELECT 1 FROM copilot.run_events WHERE run_id = :run_id AND key = :key")
_ADD_EVENT = text(
    """
    INSERT INTO copilot.run_events (run_id, seq, type, key, payload)
    VALUES (:run_id, :seq, :type, :key, CAST(:payload AS jsonb))
    """
)
_SET_SEQ = text("UPDATE copilot.runs SET last_seq = :seq WHERE id = :run_id")
_EVENTS_AFTER = text(
    """
    SELECT run_id, seq, type, payload, created_at FROM copilot.run_events
    WHERE run_id = :run_id AND seq > :seq ORDER BY seq LIMIT :limit
    """
)


def _run(row: Row[Any]) -> Run:
    return Run(
        id=row.id,
        person=Person(
            oid=row.user_oid, name=row.user_name, roles=tuple(row.roles), region=row.region
        ),
        ticket_id=row.ticket_id,
        order_id=row.order_id,
        status=row.status,
        message=row.message,
        model=row.model,
        tokens_in=row.tokens_in,
        tokens_out=row.tokens_out,
        tokens_cached=row.tokens_cached,
        estimated_cost_inr=row.estimated_cost_inr,
        tool_calls=row.tool_calls,
        model_turns=row.model_turns,
        elapsed_ms=row.elapsed_ms,
        result=row.result,
        stop_reason=row.stop_reason,
        created_at=row.created_at,
        finished_at=row.finished_at,
    )


def _action(row: Row[Any]) -> RunAction:
    return RunAction(
        action_id=row.action_id,
        run_id=row.run_id,
        call_id=row.call_id,
        tool=row.tool,
        idempotency_key=row.idempotency_key,
        expires_at=row.expires_at,
        decision=row.decision,
        decided_by=row.decided_by,
        decision_reason=row.decision_reason,
    )


class PostgresRunStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def _write(self, statement: Any, parameters: dict[str, Any]) -> bool:
        async with self._engine.begin() as connection:
            return (await connection.execute(statement, parameters)).rowcount > 0

    async def _rows(self, statement: Any, parameters: dict[str, Any]) -> list[Row[Any]]:
        async with self._engine.connect() as connection:
            return list((await connection.execute(statement, parameters)).all())

    async def create(self, run: NewRun) -> None:
        await self._write(
            _CREATE,
            {
                "id": run.id,
                "user_oid": run.person.oid,
                "user_name": run.person.name,
                "roles": list(run.person.roles),
                "region": run.person.region,
                "ticket_id": run.ticket_id,
                "order_id": run.order_id,
                "message": run.message,
                "model": run.model,
            },
        )

    async def get(self, run_id: UUID) -> Run | None:
        rows = await self._rows(_GET, {"id": run_id})
        return _run(rows[0]) if rows else None

    async def recent_for(self, user_oid: UUID, *, ask_only: bool, limit: int) -> list[Run]:
        parameters = {"user_oid": user_oid, "ask_only": ask_only, "limit": limit}
        return [_run(row) for row in await self._rows(_RECENT_FOR, parameters)]

    async def on_ticket(self, ticket_id: int, limit: int) -> list[Run]:
        rows = await self._rows(_ON_TICKET, {"ticket_id": ticket_id, "limit": limit})
        return [_run(row) for row in rows]

    async def recent(self, limit: int) -> list[Run]:
        return [_run(row) for row in await self._rows(_RECENT, {"limit": limit})]

    async def latest_id(self) -> UUID | None:
        rows = await self._rows(_LATEST, {})
        return rows[0].id if rows else None

    async def claim(
        self, run_id: UUID, owner: UUID, lease_s: int, held: tuple[RunStatus, ...]
    ) -> int | None:
        async with self._engine.begin() as connection:
            row = (
                await connection.execute(
                    _CLAIM, {"id": run_id, "owner": owner, "lease_s": lease_s, "held": list(held)}
                )
            ).first()
        return None if row is None else row.attempt

    async def heartbeat(self, run_id: UUID, owner: UUID, lease_s: int) -> bool:
        return await self._write(_HEARTBEAT, {"id": run_id, "owner": owner, "lease_s": lease_s})

    async def settle(self, run_id: UUID, owner: UUID, outcome: Outcome) -> bool:
        return await self._write(
            _SETTLE,
            {
                "id": run_id,
                "owner": owner,
                "status": outcome.status,
                "result": None if outcome.result is None else json.dumps(outcome.result),
                "stop_reason": outcome.stop_reason,
                "finished": list(_FINISHED),
            },
        )

    async def cancel(self, run_id: UUID, user_oid: UUID) -> bool:
        return await self._write(
            _CANCEL, {"id": run_id, "user_oid": user_oid, "unfinished": list(UNFINISHED)}
        )

    async def record_model_call(self, run_id: UUID, call: ModelCall) -> None:
        usage = {
            "run_id": run_id,
            "tokens_in": call.usage.tokens_in,
            "tokens_out": call.usage.tokens_out,
            "tokens_cached": call.usage.tokens_cached,
        }
        async with self._engine.begin() as connection:
            await connection.execute(
                _ADD_MODEL_CALL,
                usage | {"node": call.node, "model": call.model, "latency_ms": call.latency_ms},
            )
            await connection.execute(_ADD_USAGE, usage | {"cost": call.cost_inr})

    async def record_progress(self, run_id: UUID, progress: Progress) -> None:
        await self._write(
            _PROGRESS,
            {
                "run_id": run_id,
                "tool_calls": progress.tool_calls,
                "model_turns": progress.model_turns,
                "elapsed_ms": progress.elapsed_ms,
            },
        )

    async def record_guardrail(self, run_id: UUID, triggered: Triggered) -> None:
        await self._write(
            _ADD_GUARDRAIL,
            {
                "run_id": run_id,
                "layer": triggered.layer,
                "rule": triggered.rule,
                "severity": triggered.severity,
                "action": triggered.action,
                "detail": triggered.detail,
            },
        )

    async def add_action(self, action: RunAction) -> None:
        await self._write(
            _ADD_ACTION,
            {
                "action_id": action.action_id,
                "run_id": action.run_id,
                "call_id": action.call_id,
                "tool": action.tool,
                "idempotency_key": action.idempotency_key,
                "expires_at": action.expires_at,
            },
        )

    async def note_decision(
        self, action_id: UUID, decision: Decision, by: str, reason: str | None
    ) -> None:
        await self._write(
            _NOTE_DECISION,
            {"action_id": action_id, "decision": decision, "by": by, "reason": reason},
        )

    async def action(self, action_id: UUID) -> RunAction | None:
        rows = await self._rows(_ACTION, {"action_id": action_id})
        return _action(rows[0]) if rows else None

    async def actions_of(self, run_id: UUID) -> list[RunAction]:
        return [_action(row) for row in await self._rows(_ACTIONS_OF, {"run_id": run_id})]

    async def append_event(
        self, run_id: UUID, kind: str, key: str, payload: dict[str, Any]
    ) -> int | None:
        async with self._engine.begin() as connection:
            locked = (await connection.execute(_LOCK_RUN, {"run_id": run_id})).first()
            if locked is None:
                return None
            if (await connection.execute(_HAS_KEY, {"run_id": run_id, "key": key})).first():
                return None
            seq = locked.last_seq + 1
            await connection.execute(
                _ADD_EVENT,
                {
                    "run_id": run_id,
                    "seq": seq,
                    "type": kind,
                    "key": key,
                    "payload": json.dumps(payload),
                },
            )
            await connection.execute(_SET_SEQ, {"run_id": run_id, "seq": seq})
        return seq

    async def events_after(self, run_id: UUID, seq: int, limit: int) -> list[StoredEvent]:
        rows = await self._rows(_EVENTS_AFTER, {"run_id": run_id, "seq": seq, "limit": limit})
        return [
            StoredEvent(
                run_id=str(row.run_id),
                seq=row.seq,
                type=row.type,
                payload=row.payload,
                created_at=row.created_at,
            )
            for row in rows
        ]

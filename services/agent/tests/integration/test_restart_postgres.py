"""A run saved in PostgreSQL survives the process: the done-when of the agent phase.

The run's steps are saved by the LangGraph checkpointer and its row and events by the run
store, both in the agent's own database. The second process shares nothing with the first
but that database.
"""

import asyncio
import json
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import UUID

import pytest
from psycopg import AsyncConnection
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from opscp_agent.adapters.checkpoints import SCHEMA, Checkpoints, checkpoint_pool
from opscp_agent.adapters.postgres import PostgresRunStore
from opscp_agent.events import types as events
from opscp_agent_testing.desk import CANARY, bearer, desk, mcp_backend, token_for
from opscp_agent_testing.models import Stalls, Told, reads, says
from opscp_agent_testing.reference import FIRST_HALF, SECOND_HALF, TICKET
from opscp_common.limits import Limits
from opscp_mcp_testing.identities import ARJUN, PRIYA

from .conftest import database_settings

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("empty_tables")]

WAIT_S = 15
_TABLES = text(
    "SELECT tablename, tableowner FROM pg_tables WHERE schemaname = :schema ORDER BY tablename"
)
_SAVED = text(
    "SELECT string_agg(c::text, ' ') FROM ("
    " SELECT checkpoint::text || metadata::text AS c FROM langgraph.checkpoints"
    " UNION ALL SELECT encode(blob, 'escape') FROM langgraph.checkpoint_blobs"
    " UNION ALL SELECT encode(blob, 'escape') FROM langgraph.checkpoint_writes) saved"
)


@asynccontextmanager
async def _process() -> AsyncGenerator[Checkpoints]:
    """What one process holds: its own connections to the agent's database."""
    pool = checkpoint_pool(database_settings("agent_app"), "local", 4, 3)
    async with pool:
        checkpoints = Checkpoints(pool)
        assert await checkpoints.ready()
        yield checkpoints


async def _status(store: PostgresRunStore, run_id: UUID, *wanted: str) -> str:
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        run = await store.get(run_id)
        assert run is not None
        if run.status in wanted:
            return run.status
        await asyncio.sleep(0.05)
    raise AssertionError(f"the run never became one of {wanted}")


async def _kinds(store: PostgresRunStore, run_id: UUID) -> list[str]:
    return [event.type for event in await store.events_after(run_id, 0, 500)]


async def test_the_checkpointer_makes_its_own_tables_in_its_own_schema(
    agent_app: AsyncEngine,
) -> None:
    async with _process() as checkpoints:
        assert await checkpoints.ready()

    async with agent_app.connect() as connection:
        tables = (await connection.execute(_TABLES, {"schema": SCHEMA})).all()
        elsewhere = (await connection.execute(_TABLES, {"schema": "public"})).all()

    assert {name for name, _owner in tables} >= {
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
        "checkpoint_migrations",
    }
    assert {owner for _name, owner in tables} == {"agent_app"}
    assert [name for name, _owner in elsewhere] == ["alembic_version"]


async def test_a_run_waiting_for_approval_is_approved_and_finished_by_another_process(
    limits: Limits, agent_app: AsyncEngine
) -> None:
    store = PostgresRunStore(agent_app)
    backend = mcp_backend(limits, SECOND_HALF)
    async with (
        _process() as first,
        desk(limits, SECOND_HALF, saver=first.saver, store=store, backend=backend) as at,
    ):
        response = await at.agent.post(
            "/api/runs",
            json={"message": "Investigate this ticket.", "ticket_id": TICKET},
            headers=bearer(ARJUN),
        )
        run_id = UUID(response.json()["run_id"])
        await _status(store, run_id, "awaiting_approval")
        await at.manager.idle()
        before = await _kinds(store, run_id)

    assert before.count(events.APPROVAL_REQUIRED) == 2
    assert backend.written.refunds == []

    # Everything of the first process is gone. A second one starts with the same database.
    async with (
        _process() as second,
        desk(limits, SECOND_HALF, saver=second.saver, store=store, backend=backend) as at,
    ):
        cards = [
            event.payload
            for event in await store.events_after(run_id, 0, 500)
            if event.type == events.APPROVAL_REQUIRED
        ]
        for card in cards:
            approved = await at.agent.post(
                f"/api/runs/{run_id}/actions/{card['action_id']}/approve",
                json={"args_hash": card["args_hash"]},
                headers=bearer(ARJUN),
            )
            assert approved.status_code == 200, approved.text
        await _status(store, run_id, "completed")
        await at.manager.idle()

    after = await _kinds(store, run_id)
    assert after[: len(before)] == before
    assert after.count(events.APPROVAL_REQUIRED) == 2
    assert after[-1] == events.RUN_COMPLETED
    (refund,) = backend.written.refunds
    assert refund.amount_minor == 62_000
    assert len(backend.written.notes) == 1
    done = await store.get(run_id)
    assert done is not None
    assert done.result is not None
    assert [action["status"] for action in done.result["proposed_actions"]] == ["executed"] * 2
    assert done.result["customer_reply_draft"]


async def test_a_run_killed_between_steps_goes_on_in_another_process_from_where_it_was(
    limits: Limits, agent_app: AsyncEngine
) -> None:
    store = PostgresRunStore(agent_app)
    first_model = Stalls(reads(("get_order", {"order_id": 4471})))
    async with (
        _process() as first,
        desk(limits, FIRST_HALF, saver=first.saver, store=store, model=first_model) as at,
    ):
        response = await at.agent.post(
            "/api/runs", json={"message": "What is wrong with order 4471?"}, headers=bearer(PRIYA)
        )
        run_id = UUID(response.json()["run_id"])
        await asyncio.wait_for(first_model.reached.wait(), WAIT_S)
        # The process dies with the run's second step in hand.
        await at.manager.close()

    run = await store.get(run_id)
    assert run is not None
    assert run.status == "running"
    async with agent_app.begin() as connection:
        await connection.execute(
            text(
                "UPDATE copilot.runs SET lease_until = now() - interval '1 second' WHERE id = :id"
            ),
            {"id": run_id},
        )

    second_model = Told([says("The address failed validation.")])
    async with (
        _process() as second,
        desk(limits, FIRST_HALF, saver=second.saver, store=store, model=second_model) as at,
    ):
        seen = await at.agent.get(f"/api/runs/{run_id}/events", headers=bearer(PRIYA))
        assert seen.status_code == 200
        await _status(store, run_id, "completed")
        await at.manager.idle()

        kinds = await _kinds(store, run_id)
        assert kinds.count(events.RUN_STARTED) == 1
        assert kinds.count(events.TOOL_STARTED) == 1
        assert kinds[-1] == events.RUN_COMPLETED
        shown = [m for m in second_model.requests[0].messages if m["role"] == "tool"]
        assert len(shown) == 1
        assert at.audit.entries == []
        done = await store.get(run_id)
        assert done is not None
        assert done.result is not None
        assert done.result["root_cause"] == "The address failed validation."
        assert done.model_turns == 2


async def test_nothing_saved_for_a_run_holds_a_token_or_the_canary(
    limits: Limits, agent_app: AsyncEngine
) -> None:
    store = PostgresRunStore(agent_app)
    async with (
        _process() as process,
        desk(limits, FIRST_HALF, saver=process.saver, store=store) as at,
    ):
        response = await at.agent.post(
            "/api/runs",
            json={"message": "Investigate this ticket.", "ticket_id": TICKET},
            headers=bearer(PRIYA),
        )
        run_id = UUID(response.json()["run_id"])
        await _status(store, run_id, "awaiting_approval")
        await at.manager.idle()

    async with agent_app.connect() as connection:
        saved = await connection.scalar(_SAVED)
        rows = (
            await connection.execute(
                text("SELECT row_to_json(r)::text FROM copilot.runs r WHERE id = :id"),
                {"id": run_id},
            )
        ).scalar_one()
    stored = json.dumps([event.payload for event in await store.events_after(run_id, 0, 500)])

    assert saved
    assert "get_order" in saved
    for kept in (saved, rows, stored):
        assert CANARY not in kept
        assert token_for(PRIYA).split(".")[1][:40] not in kept
        assert "eyJhbGciOi" not in kept


async def test_the_pool_reaches_only_the_checkpoint_schema() -> None:
    pool = checkpoint_pool(database_settings("agent_app"), "local", 2, 3)
    async with pool, pool.connection() as connection:
        assert isinstance(connection, AsyncConnection)
        row = await (await connection.execute("SHOW search_path")).fetchone()

    assert row == {"search_path": SCHEMA}

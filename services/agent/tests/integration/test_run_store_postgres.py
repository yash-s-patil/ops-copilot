"""The run store in PostgreSQL, and what the agent's database role may and may not do."""

import asyncio
from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from opscp_agent.adapters.postgres import PostgresRunStore
from opscp_agent.runs.store import RunStore
from opscp_agent_testing.store_cases import (
    CASES,
    PRIYA,
    a_run_whose_holder_has_gone_can_be_taken_up,
    new_run,
)

from .conftest import database_settings

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("empty_tables")]

_LAPSE = text("UPDATE copilot.runs SET lease_until = now() - interval '1 second' WHERE id = :id")


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.__name__)
async def test_the_database_store(
    agent_app: AsyncEngine, case: Callable[[RunStore], Awaitable[None]]
) -> None:
    await case(PostgresRunStore(agent_app))


async def test_a_lapsed_lease_in_the_database(agent_app: AsyncEngine) -> None:
    async def lapse(run_id: UUID) -> None:
        async with agent_app.begin() as connection:
            await connection.execute(_LAPSE, {"id": run_id})

    await a_run_whose_holder_has_gone_can_be_taken_up(PostgresRunStore(agent_app), lapse)


async def test_two_processes_claiming_one_run_at_once_do_not_both_get_it(
    agent_app: AsyncEngine,
) -> None:
    store = PostgresRunStore(agent_app)
    run_id = await new_run(store)

    claims = await asyncio.gather(
        *(store.claim(run_id, uuid4(), 30, ("queued",)) for _ in range(8))
    )

    assert sorted(claim for claim in claims if claim is not None) == [1]


async def test_events_written_at_once_are_still_numbered_without_gaps(
    agent_app: AsyncEngine,
) -> None:
    store = PostgresRunStore(agent_app)
    run_id = await new_run(store)

    numbers = await asyncio.gather(
        *(store.append_event(run_id, "tool.started", f"k{n % 10}", {}) for n in range(30))
    )

    assert sorted(number for number in numbers if number is not None) == list(range(1, 11))
    stored = await store.events_after(run_id, 0, 100)
    assert [event.seq for event in stored] == list(range(1, 11))


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE copilot.run_events SET payload = '{}'::jsonb",
        "DELETE FROM copilot.run_events",
        "DELETE FROM copilot.runs",
        "UPDATE copilot.runs SET message = 'changed'",
        "UPDATE copilot.runs SET user_oid = gen_random_uuid()",
        "UPDATE copilot.runs SET ticket_id = 1",
        "UPDATE copilot.llm_calls SET tokens_in = 0",
        "DELETE FROM copilot.guardrail_events",
        "UPDATE copilot.run_actions SET idempotency_key = 'other'",
        "TRUNCATE copilot.run_events",
        "CREATE TABLE copilot.extra (id int)",
        "DROP TABLE copilot.runs",
    ],
)
async def test_the_agent_cannot_change_or_remove_what_it_has_recorded(
    agent_app: AsyncEngine, statement: str
) -> None:
    store = PostgresRunStore(agent_app)
    run_id = await new_run(store)
    await store.append_event(run_id, "run.started", "run.started", {})

    with pytest.raises(ProgrammingError, match=r"permission denied|must be owner"):
        async with agent_app.begin() as connection:
            await connection.execute(text(statement))


@pytest.mark.parametrize("database", ["meridian", "meridian_test"])
async def test_the_agent_has_no_way_into_the_business_database(database: str) -> None:
    engine = create_async_engine(database_settings("agent_app", database).url())
    try:
        with pytest.raises(DBAPIError, match="permission denied for database"):
            async with engine.connect():
                pass
    finally:
        await engine.dispose()


@pytest.mark.parametrize("role", ["mcp_app", "migrator", "payments_worker", "postgres"])
async def test_the_agent_s_password_opens_no_other_role(role: str) -> None:
    held = database_settings("agent_app").password
    as_another = database_settings("migrator", "meridian").model_copy(
        update={"user": role, "password": held}
    )
    engine = create_async_engine(as_another.url())
    try:
        with pytest.raises(DBAPIError, match="password authentication failed"):
            async with engine.connect():
                pass
    finally:
        await engine.dispose()


async def test_a_run_s_status_is_one_the_database_knows(owner: AsyncEngine) -> None:
    store = PostgresRunStore(owner)
    run_id = await new_run(store, PRIYA)

    with pytest.raises(DBAPIError, match="runs_status_check"):
        async with owner.begin() as connection:
            await connection.execute(
                text("UPDATE copilot.runs SET status = 'paused' WHERE id = :id"), {"id": run_id}
            )
    with pytest.raises(DBAPIError, match="runs_finished_check"):
        async with owner.begin() as connection:
            await connection.execute(
                text("UPDATE copilot.runs SET status = 'completed' WHERE id = :id"), {"id": run_id}
            )

"""The in-memory run store keeps the rules the tests of the database's store check."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID

import pytest

from opscp_agent.runs.store import RunStore
from opscp_agent_testing.memory_store import InMemoryRunStore
from opscp_agent_testing.store_cases import CASES, a_run_whose_holder_has_gone_can_be_taken_up


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.__name__)
async def test_the_memory_store(case: Callable[[RunStore], Awaitable[None]]) -> None:
    await case(InMemoryRunStore())


async def test_a_lapsed_lease_in_the_memory_store() -> None:
    store = InMemoryRunStore()

    async def lapse(run_id: UUID) -> None:
        store.leases[run_id].until = datetime(2000, 1, 1, tzinfo=UTC)

    await a_run_whose_holder_has_gone_can_be_taken_up(store, lapse)

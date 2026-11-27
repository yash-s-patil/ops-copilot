"""A run survives the process that was working on it."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from opscp_agent.auth.verifier import Caller, Person
from opscp_agent.events import types as events
from opscp_agent.runs.manager import RunManager
from opscp_agent_testing.desk import WAIT_S, Desk, bearer, desk, token_for
from opscp_agent_testing.memory_store import InMemoryRunStore
from opscp_agent_testing.models import Stalls, Told, reads, says
from opscp_agent_testing.reference import FIRST_HALF, FRESH_ORDER, TICKET
from opscp_common.limits import Limits
from opscp_mcp.authz.identity import Identity
from opscp_mcp_testing.identities import ARJUN, PRIYA

ASK = f"What is wrong with order {FRESH_ORDER}?"
ORDER_READ = ("get_order", {"order_id": FRESH_ORDER})
LONG_AGO = datetime(2000, 1, 1, tzinfo=UTC)


def _caller(identity: Identity = PRIYA) -> Caller:
    person = Person(
        oid=identity.oid,
        name=identity.name,
        roles=tuple(role.value for role in identity.roles),
        region=identity.region,
    )
    return Caller(person=person, token=token_for(identity))


async def _killed(at: Desk, run_id: UUID) -> None:
    """The process dies: its work stops where it was and its lease is left to lapse."""
    await at.manager.close()
    assert at.store.runs[run_id].status == "running"
    at.store.leases[run_id].until = LONG_AGO


async def test_a_run_goes_on_from_its_last_saved_step_in_another_process(limits: Limits) -> None:
    stalls = Stalls(reads(ORDER_READ))
    async with desk(limits, FIRST_HALF, model=stalls) as at:
        run_id = await at.start(PRIYA, ASK)
        await stalls.reached.wait()
        await _killed(at, run_id)
        before = at.store.kinds(run_id)

        second = RunManager(replace(at.workings, model=Told([says("Found it.")])))
        run = at.store.runs[run_id]
        await second.nudge(run, _caller())
        await second.idle()

        assert at.store.runs[run_id].status == "completed"
        after = at.store.kinds(run_id)
        assert after[: len(before)] == before
        assert after.count(events.TOOL_STARTED) == 1
        assert after.count(events.RUN_STARTED) == 1
        reads_made = [entry for entry in at.audit.entries if entry.target == "get_order"]
        assert len(reads_made) == 1
        result = at.store.runs[run_id].result
        assert result is not None
        assert result["root_cause"] == "Found it."
        await second.close()


async def test_a_run_waiting_for_approval_is_decided_and_finished_after_a_restart(
    limits: Limits,
) -> None:
    async with desk(limits, FIRST_HALF) as at:
        run_id = await at.start(PRIYA, "Investigate.", TICKET)
        await at.settled(run_id, "awaiting_approval")
        await at.manager.close()

        await at.decide(PRIYA, run_id)
        await at.settled(run_id, "completed")

        assert at.written.tickets[TICKET].queue == "L2"
        assert at.store.kinds(run_id).count(events.APPROVAL_REQUIRED) == 1


async def test_a_run_that_died_as_it_began_to_wait_still_waits_when_picked_up(
    limits: Limits,
) -> None:
    async with desk(limits, FIRST_HALF) as at:
        run_id = await at.start(PRIYA, "Investigate.", TICKET)
        await at.settled(run_id, "awaiting_approval")
        # As if the process died after saving the step and before recording that it waits.
        at.store.runs[run_id] = replace(at.store.runs[run_id], status="running")

        seen = await at.agent.get(f"/api/runs/{run_id}", headers=bearer(PRIYA))
        assert seen.status_code == 200
        await at.settled(run_id, "awaiting_approval")

        assert at.store.payloads(run_id, events.RUN_FAILED) == []
        await at.decide(PRIYA, run_id)
        await at.settled(run_id, "completed")


async def test_only_the_owner_looking_at_a_run_picks_it_up(limits: Limits) -> None:
    stalls = Stalls(reads(ORDER_READ))
    async with desk(limits, FIRST_HALF, model=stalls) as at:
        run_id = await at.start(PRIYA, ASK)
        await stalls.reached.wait()
        await _killed(at, run_id)

        await at.manager.nudge(at.store.runs[run_id], _caller(ARJUN))

        assert run_id not in at.store.leases or at.store.leases[run_id].until == LONG_AGO
        assert at.store.attempts[run_id] == 1


async def test_a_run_that_is_being_worked_on_is_not_started_a_second_time(limits: Limits) -> None:
    stalls = Stalls(reads(ORDER_READ))
    async with desk(limits, FIRST_HALF, model=stalls) as at:
        run_id = await at.start(PRIYA, ASK)
        await stalls.reached.wait()

        for _ in range(3):
            await at.agent.get(f"/api/runs/{run_id}", headers=bearer(PRIYA))

        assert at.store.attempts[run_id] == 1
        assert stalls.asked == 2


async def test_work_stops_when_its_lease_is_lost(limits: Limits) -> None:
    quick = limits.model_copy(
        update={"runs": limits.runs.model_copy(update={"heartbeat_s": 1, "lease_s": 2})}
    )
    stalls = Stalls(reads(ORDER_READ))
    async with desk(quick, FIRST_HALF, model=stalls) as at:
        run_id = await at.start(PRIYA, ASK)
        await stalls.reached.wait()

        cancelled = await at.agent.post(f"/api/runs/{run_id}/cancel", headers=bearer(PRIYA))
        assert cancelled.json()["stopped"] is True
        await at.manager.idle()

        assert at.store.runs[run_id].status == "cancelled"
        assert at.store.kinds(run_id)[-1] == events.RUN_STOPPED
        assert at.store.runs[run_id].result is None


class _FlakyBeat(InMemoryRunStore):
    """A store whose first heartbeat fails, as a database does for a moment."""

    def __init__(self) -> None:
        super().__init__()
        self.beats = 0
        self.kept_after_the_failure = asyncio.Event()

    async def heartbeat(self, run_id: UUID, owner: UUID, lease_s: int) -> bool:
        self.beats += 1
        if self.beats == 1:
            raise ConnectionError("the database went away")
        held = await super().heartbeat(run_id, owner, lease_s)
        self.kept_after_the_failure.set()
        return held


async def test_one_failed_heartbeat_does_not_end_the_lease_keeping(limits: Limits) -> None:
    quick = limits.model_copy(
        update={"runs": limits.runs.model_copy(update={"heartbeat_s": 1, "lease_s": 2})}
    )
    stalls = Stalls(reads(ORDER_READ))
    store = _FlakyBeat()
    async with desk(quick, FIRST_HALF, model=stalls, store=store) as at:
        run_id = await at.start(PRIYA, ASK)
        await stalls.reached.wait()
        async with asyncio.timeout(WAIT_S):
            await store.kept_after_the_failure.wait()

        # Its owner looks at it meanwhile. The lease is held, so it is not started again.
        await at.agent.get(f"/api/runs/{run_id}", headers=bearer(PRIYA))

        assert store.attempts[run_id] == 1
        assert stalls.asked == 2
        assert store.runs[run_id].status == "running"

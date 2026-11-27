"""What any run store must do. Run against the one in memory and the one in PostgreSQL."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from opscp_agent.auth.verifier import Person
from opscp_agent.guardrails.output import Triggered
from opscp_agent.llm.base import Usage
from opscp_agent.runs.store import (
    RESUMABLE,
    ModelCall,
    NewRun,
    Outcome,
    Progress,
    RunAction,
    RunStore,
)

PRIYA = Person(
    oid=UUID("0f1e2d3c-0001-4000-8000-000000000001"),
    name="Priya Nair",
    roles=("Support.L1",),
    region="west",
)
ARJUN = Person(
    oid=UUID("0f1e2d3c-0003-4000-8000-000000000003"),
    name="Arjun Mehta",
    roles=("Support.L2",),
    region="west",
)
LEASE_S = 30
SOON = datetime(2030, 1, 1, tzinfo=UTC)
QUEUED = ("queued",)


async def new_run(store: RunStore, person: Person = PRIYA, ticket_id: int | None = None) -> UUID:
    run_id = uuid4()
    await store.create(
        NewRun(
            id=run_id,
            person=person,
            ticket_id=ticket_id,
            order_id=None if ticket_id is None else 4471,
            message="Investigate.",
            model="scripted",
        )
    )
    return run_id


async def a_new_run_is_kept_as_it_was_given(store: RunStore) -> None:
    run_id = await new_run(store, ticket_id=2207)

    run = await store.get(run_id)

    assert run is not None
    assert (run.person, run.ticket_id, run.order_id) == (PRIYA, 2207, 4471)
    assert (run.status, run.message, run.model) == ("queued", "Investigate.", "scripted")
    assert (run.tokens_in, run.tool_calls, run.estimated_cost_inr) == (0, 0, None)
    assert (run.result, run.finished_at) == (None, None)
    assert await store.get(uuid4()) is None


async def events_are_numbered_in_order_and_a_key_is_stored_once(store: RunStore) -> None:
    run_id = await new_run(store)

    numbers = [
        await store.append_event(run_id, "tool.started", "tool.started:c1", {"n": 1}),
        await store.append_event(run_id, "tool.finished", "tool.finished:c1", {"n": 2}),
        await store.append_event(run_id, "tool.started", "tool.started:c1", {"n": 99}),
        await store.append_event(run_id, "run.completed", "run.completed", {"n": 3}),
    ]

    assert numbers == [1, 2, None, 3]
    stored = await store.events_after(run_id, 0, 10)
    assert [(event.seq, event.type, event.payload) for event in stored] == [
        (1, "tool.started", {"n": 1}),
        (2, "tool.finished", {"n": 2}),
        (3, "run.completed", {"n": 3}),
    ]
    assert [event.seq for event in await store.events_after(run_id, 1, 1)] == [2]
    assert await store.events_after(run_id, 3, 10) == []
    assert await store.append_event(uuid4(), "x", "k", {}) is None


async def one_run_s_events_are_not_another_s(store: RunStore) -> None:
    first, second = await new_run(store), await new_run(store)

    await store.append_event(first, "run.started", "run.started", {})
    assert await store.append_event(second, "run.started", "run.started", {}) == 1
    assert len(await store.events_after(first, 0, 10)) == 1


async def a_run_is_worked_on_by_one_holder_at_a_time(store: RunStore) -> None:
    run_id = await new_run(store)
    here, there = uuid4(), uuid4()

    assert await store.claim(run_id, here, LEASE_S, QUEUED) == 1
    assert await store.claim(run_id, there, LEASE_S, RESUMABLE) is None
    assert await store.claim(run_id, here, LEASE_S, RESUMABLE) is None
    run = await store.get(run_id)
    assert run is not None
    assert run.status == "running"
    assert await store.heartbeat(run_id, here, LEASE_S)
    assert not await store.heartbeat(run_id, there, LEASE_S)
    assert not await store.settle(run_id, there, Outcome(status="completed"))


async def a_run_whose_holder_has_gone_can_be_taken_up(
    store: RunStore, lapse: Callable[[UUID], Awaitable[None]]
) -> None:
    run_id = await new_run(store)
    gone, here = uuid4(), uuid4()
    await store.claim(run_id, gone, LEASE_S, QUEUED)

    await lapse(run_id)

    assert await store.claim(run_id, here, LEASE_S, QUEUED) is None
    assert await store.claim(run_id, here, LEASE_S, RESUMABLE) == 2
    assert not await store.heartbeat(run_id, gone, LEASE_S)
    assert not await store.settle(run_id, gone, Outcome(status="failed"))
    assert await store.settle(run_id, here, Outcome(status="completed", result={"ok": True}))


async def a_settled_run_is_given_up_in_its_new_status(store: RunStore) -> None:
    run_id = await new_run(store)
    here = uuid4()
    await store.claim(run_id, here, LEASE_S, QUEUED)

    assert await store.settle(run_id, here, Outcome(status="awaiting_approval"))
    waiting = await store.get(run_id)
    assert waiting is not None
    assert (waiting.status, waiting.finished_at) == ("awaiting_approval", None)
    assert await store.claim(run_id, here, LEASE_S, RESUMABLE) is None
    assert await store.claim(run_id, uuid4(), LEASE_S, ("awaiting_approval",)) == 2


async def a_finished_run_keeps_its_result_and_cannot_be_taken_again(store: RunStore) -> None:
    run_id = await new_run(store)
    here = uuid4()
    await store.claim(run_id, here, LEASE_S, QUEUED)
    outcome = Outcome(status="stopped", result={"root_cause": "x"}, stop_reason="Too long.")

    assert await store.settle(run_id, here, outcome)

    run = await store.get(run_id)
    assert run is not None
    assert (run.status, run.result, run.stop_reason) == (
        "stopped",
        {"root_cause": "x"},
        "Too long.",
    )
    assert run.finished_at is not None
    assert await store.claim(run_id, here, LEASE_S, (*RESUMABLE, "awaiting_approval")) is None
    assert not await store.cancel(run_id, PRIYA.oid)


async def only_its_owner_can_cancel_a_run_and_only_while_it_is_unfinished(
    store: RunStore,
) -> None:
    run_id = await new_run(store)
    here = uuid4()
    await store.claim(run_id, here, LEASE_S, QUEUED)

    assert not await store.cancel(run_id, ARJUN.oid)
    assert await store.cancel(run_id, PRIYA.oid)
    assert not await store.cancel(run_id, PRIYA.oid)
    run = await store.get(run_id)
    assert run is not None
    assert run.status == "cancelled"
    assert run.finished_at is not None
    assert not await store.heartbeat(run_id, here, LEASE_S)
    assert not await store.settle(run_id, here, Outcome(status="completed", result={"late": 1}))
    assert (await store.get(run_id)).result is None  # pyright: ignore[reportOptionalMemberAccess] -- read above


async def usage_adds_up_and_an_unknown_cost_stays_unknown(store: RunStore) -> None:
    run_id = await new_run(store)
    usage = Usage(tokens_in=100, tokens_out=10, tokens_cached=40)

    def call(cost: Decimal | None) -> ModelCall:
        return ModelCall(node="reason", model="m", usage=usage, latency_ms=5, cost_inr=cost)

    await store.record_model_call(run_id, call(None))
    unknown = await store.get(run_id)
    await store.record_model_call(run_id, call(Decimal("1.25")))
    await store.record_model_call(run_id, call(Decimal("0.50")))
    await store.record_progress(run_id, Progress(tool_calls=6, model_turns=3, elapsed_ms=1500))

    run = await store.get(run_id)
    assert unknown is not None
    assert run is not None
    assert unknown.estimated_cost_inr is None
    assert (run.tokens_in, run.tokens_out, run.tokens_cached) == (300, 30, 120)
    assert run.estimated_cost_inr == Decimal("1.75")
    assert (run.tool_calls, run.model_turns, run.elapsed_ms) == (6, 3, 1500)


async def runs_are_listed_newest_first_for_their_owner_a_ticket_or_everyone(
    store: RunStore,
) -> None:
    asked = await new_run(store)
    on_ticket = await new_run(store, ticket_id=2207)
    others = await new_run(store, ARJUN, ticket_id=2207)

    mine = await store.recent_for(PRIYA.oid, ask_only=False, limit=10)
    assert [run.id for run in mine] == [on_ticket, asked]
    ask_only = await store.recent_for(PRIYA.oid, ask_only=True, limit=10)
    assert [run.id for run in ask_only] == [asked]
    assert [run.id for run in await store.on_ticket(2207, 10)] == [others, on_ticket]
    assert [run.id for run in await store.recent(2)] == [others, on_ticket]
    assert await store.latest_id() == others
    assert await store.on_ticket(9999, 10) == []


async def a_change_a_run_asked_for_is_found_by_its_id(store: RunStore) -> None:
    run_id = await new_run(store)
    action = RunAction(
        action_id=uuid4(),
        run_id=run_id,
        call_id="c1",
        tool="issue_refund",
        idempotency_key="k1",
        expires_at=SOON,
    )

    await store.add_action(action)
    await store.add_action(action)

    assert await store.action(action.action_id) == action
    assert await store.actions_of(run_id) == [action]
    assert await store.action(uuid4()) is None
    assert action.is_open(SOON - timedelta(seconds=1))
    assert not action.is_open(SOON)


async def the_first_decision_on_a_change_is_the_one_that_is_kept(store: RunStore) -> None:
    run_id = await new_run(store)
    action = RunAction(
        action_id=uuid4(),
        run_id=run_id,
        call_id="c1",
        tool="issue_refund",
        idempotency_key="k1",
        expires_at=SOON,
    )
    await store.add_action(action)

    await store.note_decision(action.action_id, "rejected", "Kavita Rao", "Too much.")
    await store.note_decision(action.action_id, "approved", "Arjun Mehta", None)
    await store.note_decision(uuid4(), "approved", "Nobody", None)

    noted = await store.action(action.action_id)
    assert noted is not None
    assert (noted.decision, noted.decided_by, noted.decision_reason) == (
        "rejected",
        "Kavita Rao",
        "Too much.",
    )
    assert not noted.is_open(SOON - timedelta(hours=1))


async def a_guardrail_event_is_kept(store: RunStore) -> None:
    run_id = await new_run(store)

    await store.record_guardrail(
        run_id,
        Triggered(layer="G8", rule="canary", severity="high", action="stopped", detail="x"),
    )


CASES = (
    a_new_run_is_kept_as_it_was_given,
    events_are_numbered_in_order_and_a_key_is_stored_once,
    one_run_s_events_are_not_another_s,
    a_run_is_worked_on_by_one_holder_at_a_time,
    a_settled_run_is_given_up_in_its_new_status,
    a_finished_run_keeps_its_result_and_cannot_be_taken_again,
    only_its_owner_can_cancel_a_run_and_only_while_it_is_unfinished,
    usage_adds_up_and_an_unknown_cost_stays_unknown,
    runs_are_listed_newest_first_for_their_owner_a_ticket_or_everyone,
    a_change_a_run_asked_for_is_found_by_its_id,
    the_first_decision_on_a_change_is_the_one_that_is_kept,
    a_guardrail_event_is_kept,
)

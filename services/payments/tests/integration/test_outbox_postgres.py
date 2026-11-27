"""The worker against PostgreSQL, connected as the role `payments_worker`."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from opscp_common.limits import Limits
from opscp_payments.adapters.postgres import OutOfStepError, PostgresOutbox
from opscp_payments.ports.outbox import Handoff, Step, Work
from opscp_payments.sim.store import Store
from opscp_payments.worker.audit import entry_for
from opscp_payments.worker.outbox import Handoffs
from opscp_payments_testing.fakes import CrashedBeforeCommitError, FakeProvider

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("refunds_to_pay")]

START = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
ENGINEER = UUID("0f1e2d3c-0003-4000-8000-000000000003")
WITH_TICKET, WITHOUT_TICKET = 301, 302
TICKET = 201
FIRST, SECOND = UUID(int=301), UUID(int=302)
FOLLOW_UP = "Refund payment failed. Needs follow-up by an L2 engineer."
_SCOPE_ALL = "SELECT set_config('app.region', '*', true)"
_ROWS = (
    "INSERT INTO core.customers (id, full_name, email, phone, tier, region) VALUES"
    " (1, 'Asha West', 'asha.west@example.in', '+91 90000 00001', 'premium', 'west')",
    "INSERT INTO core.orders (id, customer_id, status, amount_minor, region, placed_at,"
    " status_changed_at) VALUES (101, 1, 'packed', 620000, 'west', now(), now())",
    "INSERT INTO core.tickets (id, order_id, customer_id, region, priority, summary) VALUES"
    " (201, 101, 1, 'west', 'P2', 'West order is stuck')",
    "INSERT INTO control.pending_actions (id, tool, args, args_hash, requester_oid,"
    " requester_name, requester_region, approval_kind, status, approver_oid, approver_name,"
    " decided_at, executed_at, idempotency_key, order_id, expires_at)"
    " SELECT ('00000000-0000-4000-8000-000000000' || n)::uuid, 'issue_refund', '{}'::jsonb,"
    " 'hash-' || n, :engineer, 'Arjun Mehta', 'west', 'self', 'executed', :engineer,"
    " 'Arjun Mehta', now(), now(), 'key-' || n, 101, now() + interval '1 day'"
    " FROM generate_series(301, 302) AS n",
    "INSERT INTO core.refunds (id, order_id, ticket_id, region, amount_minor, reason_code,"
    " reason_text, requested_by_oid, requested_by_name, approved_by_oid, approved_by_name,"
    " pending_action_id) SELECT n, 101, CASE n WHEN 301 THEN 201 END, 'west', 62000,"
    " 'goodwill_delay', 'Delayed.', :engineer, 'Arjun Mehta', :engineer, 'Arjun Mehta',"
    " ('00000000-0000-4000-8000-000000000' || n)::uuid FROM generate_series(301, 302) AS n",
    "INSERT INTO control.outbox (id, kind, refund_id, payload, next_attempt_at, trace_id)"
    " SELECT ('00000000-0000-0000-0000-000000000' || to_hex(n))::uuid, 'refund.submit', n,"
    " '{}'::jsonb, CAST(:start AS timestamptz) + (n - 301) * interval '1 second', 'trace-' || n"
    " FROM generate_series(301, 302) AS n",
    "INSERT INTO control.chaos_flags (name) VALUES ('payment_fail')",
)
_REFUND = text(
    "SELECT status, provider_ref, paid_at, failure_reason, amount_minor FROM core.refunds"
    " WHERE id = :id"
)
_OUTBOX = text(
    "SELECT status, attempts, next_attempt_at, last_error FROM control.outbox WHERE refund_id = :id"
)
_NOTES = text(
    "SELECT ticket_id, author_kind, author_oid, author_name, body FROM core.ticket_notes"
    " ORDER BY id"
)
_AUDIT = text(
    "SELECT written_by, trace_id, actor_oid, actor_roles, actor_kind, target, action,"
    " outcome, error_code, args_redacted FROM audit.audit_log WHERE id > :after ORDER BY id"
)
_LAST_AUDIT_ID = text("SELECT coalesce(max(id), 0) FROM audit.audit_log")
_DRILL = text("SELECT enabled FROM control.chaos_flags WHERE name = 'payment_fail'")


@pytest_asyncio.fixture(loop_scope="session")
async def refunds_to_pay(owner: AsyncEngine, empty_tables: None) -> None:
    async with owner.begin() as connection:
        await connection.execute(text(_SCOPE_ALL))
        for statement in _ROWS:
            given = {"engineer": ENGINEER, "start": START}
            wanted = {name: value for name, value in given.items() if f":{name}" in statement}
            await connection.execute(text(statement), wanted)


class _Bench:
    """A worker on the real outbox, a provider the test controls, and one clock."""

    def __init__(self, owner: AsyncEngine, worker: AsyncEngine, limits: Limits) -> None:
        self.limits = limits.payments
        self.now = START + timedelta(seconds=1)
        self._owner = owner
        refs = iter(f"sim_{n}" for n in range(1, 1000))
        self.store = Store(
            settle_delay_s=limits.sim.settle_delay_s,
            capacity=limits.sim.refunds_kept,
            clock=lambda: (self.now - START).total_seconds(),
            new_ref=lambda: next(refs),
        )
        self.provider = FakeProvider(self.store)
        self.outbox = PostgresOutbox(worker)
        self.handoffs = self.worker_on(self.outbox)
        self.audit_before = 0

    def worker_on(self, outbox: Any, *, chaos_enabled: bool = False) -> Handoffs:
        return Handoffs(
            outbox,
            self.provider,
            self.limits,
            lambda: self.now,
            chaos_enabled=chaos_enabled,
            spread=lambda: 1.0,
        )

    async def poll_after(self, seconds: float = 0, handoffs: Handoffs | None = None) -> int:
        self.now += timedelta(seconds=seconds)
        return await (handoffs or self.handoffs).poll()

    async def seen(self, statement: Any, **values: Any) -> list[dict[str, Any]]:
        async with self._owner.connect() as connection:
            await connection.execute(text("SELECT set_config('app.region', '*', false)"))
            found = await connection.execute(statement, values)
            return [dict(row) for row in found.mappings().all()]

    async def refund(self, refund_id: int = WITH_TICKET) -> dict[str, Any]:
        return (await self.seen(_REFUND, id=refund_id))[0]

    async def row(self, refund_id: int = WITH_TICKET) -> dict[str, Any]:
        return (await self.seen(_OUTBOX, id=refund_id))[0]

    async def audited(self) -> list[dict[str, Any]]:
        return await self.seen(_AUDIT, after=self.audit_before)

    async def change(self, statement: str, **values: Any) -> None:
        async with self._owner.begin() as connection:
            await connection.execute(text(_SCOPE_ALL))
            await connection.execute(text(statement), values)


@pytest_asyncio.fixture(loop_scope="session")
async def bench(owner: AsyncEngine, worker: AsyncEngine, limits: Limits) -> _Bench:
    bench = _Bench(owner, worker, limits)
    bench.audit_before = (await bench.seen(_LAST_AUDIT_ID))[0]["coalesce"]
    return bench


async def test_approved_refunds_are_submitted_then_paid_with_a_note_and_two_audit_rows(
    bench: _Bench,
) -> None:
    held = await bench.poll_after()
    sent, submitted = await bench.refund(), await bench.row()

    await bench.poll_after(3)

    paid, done = await bench.refund(), await bench.row()
    assert held == 2
    assert (sent["status"], sent["provider_ref"], sent["paid_at"]) == (
        "sent_to_payments",
        "sim_1",
        None,
    )
    assert (submitted["status"], submitted["attempts"]) == ("submitted", 0)
    assert paid == {
        "status": "paid",
        "provider_ref": "sim_1",
        "paid_at": START + timedelta(seconds=4),
        "failure_reason": None,
        "amount_minor": 62000,
    }
    assert (done["status"], done["last_error"]) == ("done", None)
    assert (await bench.refund(WITHOUT_TICKET))["status"] == "paid"
    assert await bench.seen(_NOTES) == [
        {
            "ticket_id": TICKET,
            "author_kind": "system",
            "author_oid": None,
            "author_name": "Payments",
            "body": "Refund of INR 620.00 paid.",
        }
    ]
    first_submit, _, first_settle, _ = await bench.audited()
    assert first_submit == {
        "written_by": "payments_worker",
        "trace_id": "trace-301",
        "actor_oid": None,
        "actor_roles": ["Payments.Submit"],
        "actor_kind": "service",
        "target": "POST /refunds",
        "action": "submit",
        "outcome": "ok",
        "error_code": None,
        "args_redacted": {
            "refund_id": WITH_TICKET,
            "order_id": 101,
            "outbox_id": str(FIRST),
            "attempt": 1,
            "provider_ref": "sim_1",
        },
    }
    assert (first_settle["action"], first_settle["outcome"], first_settle["target"]) == (
        "settle",
        "ok",
        "GET /refunds/{provider_ref}",
    )
    assert await bench.poll_after(3600) == 0


async def test_the_failure_drill_in_the_database_declines_one_refund_and_is_used_up(
    bench: _Bench,
) -> None:
    await bench.change("UPDATE control.chaos_flags SET enabled = true")
    drilled = bench.worker_on(bench.outbox, chaos_enabled=True)

    await bench.poll_after(handoffs=drilled)
    await bench.poll_after(3, handoffs=drilled)

    failed, spent = await bench.refund(), await bench.row()
    assert (failed["status"], failed["failure_reason"], failed["paid_at"]) == (
        "failed",
        "provider_declined",
        None,
    )
    assert (spent["status"], spent["attempts"], spent["last_error"]) == (
        "failed",
        1,
        "provider_declined",
    )
    assert (await bench.refund(WITHOUT_TICKET))["status"] == "paid"
    assert [note["body"] for note in await bench.seen(_NOTES)] == [FOLLOW_UP]
    assert (await bench.seen(_DRILL))[0]["enabled"] is False
    settled = [row for row in await bench.audited() if row["action"] == "settle"]
    assert [(row["outcome"], row["error_code"]) for row in settled] == [
        ("error", "PAYMENT_DECLINED"),
        ("ok", None),
    ]


async def test_a_drill_whose_submission_failed_is_still_on_afterwards(bench: _Bench) -> None:
    await bench.change("UPDATE control.chaos_flags SET enabled = true")
    bench.provider.troubles = ["unreachable", "unreachable"]

    await bench.poll_after(handoffs=bench.worker_on(bench.outbox, chaos_enabled=True))

    assert (await bench.seen(_DRILL))[0]["enabled"] is True
    assert (await bench.row())["status"] == "pending"


async def test_a_provider_that_gives_no_answer_is_retried_and_then_the_refund_fails(
    bench: _Bench,
) -> None:
    await bench.change("DELETE FROM control.outbox WHERE refund_id = :id", id=WITHOUT_TICKET)
    bench.provider.troubles = ["timeout"] * bench.limits.max_attempts

    await bench.poll_after()
    counted = await bench.row()
    for _ in range(bench.limits.max_attempts - 1):
        await bench.poll_after(bench.limits.backoff_max_s)

    failed, spent = await bench.refund(), await bench.row()
    assert (counted["status"], counted["attempts"], counted["last_error"]) == (
        "pending",
        1,
        "timeout",
    )
    assert counted["next_attempt_at"] == bench.now - timedelta(
        seconds=bench.limits.backoff_max_s * (bench.limits.max_attempts - 1) - 1
    )
    assert (failed["status"], failed["failure_reason"], failed["provider_ref"]) == (
        "failed",
        "payment_service_unavailable",
        None,
    )
    assert (spent["status"], spent["attempts"], spent["last_error"]) == (
        "failed",
        bench.limits.max_attempts,
        "timeout",
    )
    assert [note["body"] for note in await bench.seen(_NOTES)] == [FOLLOW_UP]
    (audit,) = await bench.audited()
    assert (audit["action"], audit["outcome"], audit["error_code"]) == (
        "submit",
        "error",
        "PAYMENT_SERVICE_UNAVAILABLE",
    )


class _CrashesBeforeCommit(PostgresOutbox):
    """The provider has answered and everything is written, and then the worker dies."""

    async def step(self, outbox_id: UUID, now: datetime, work: Work) -> bool:
        async def then_crash(handoff: Handoff, step: Step) -> None:
            await work(handoff, step)
            raise CrashedBeforeCommitError

        return await super().step(outbox_id, now, then_crash)


async def test_a_crash_between_the_provider_call_and_the_commit_pays_once(
    bench: _Bench, worker: AsyncEngine
) -> None:
    await bench.change("DELETE FROM control.outbox WHERE refund_id = :id", id=WITHOUT_TICKET)

    await bench.poll_after(handoffs=bench.worker_on(_CrashesBeforeCommit(worker)))
    as_before, set_aside = await bench.refund(), await bench.row()
    too_soon = await bench.poll_after(bench.limits.backoff_max_s - 1)
    await bench.poll_after(1)
    await bench.poll_after(3)

    assert (as_before["status"], as_before["provider_ref"]) == ("approved", None)
    assert (set_aside["status"], set_aside["attempts"], set_aside["last_error"]) == (
        "pending",
        0,
        "worker_error",
    )
    assert set_aside["next_attempt_at"] == START + timedelta(seconds=1 + bench.limits.backoff_max_s)
    assert too_soon == 0
    assert len(bench.provider.submissions) == 2
    assert {s.idempotency_key for s in bench.provider.submissions} == {"refund-301"}
    assert ((await bench.refund())["status"], (await bench.refund())["provider_ref"]) == (
        "paid",
        "sim_1",
    )
    assert bench.store.find("sim_2") is None
    assert [row["action"] for row in await bench.audited()] == ["submit", "settle"]
    assert len(await bench.seen(_NOTES)) == 1


async def test_a_row_one_worker_holds_is_passed_over_by_another_without_waiting(
    bench: _Bench, worker: AsyncEngine
) -> None:
    holding, release = asyncio.Event(), asyncio.Event()
    seen_by_second: list[Handoff] = []

    async def hold(_handoff: Handoff, _step: Step) -> None:
        holding.set()
        await release.wait()

    async def take(handoff: Handoff, _step: Step) -> None:
        seen_by_second.append(handoff)

    first = asyncio.create_task(PostgresOutbox(worker).step(FIRST, bench.now, hold))
    await holding.wait()
    passed_over = await asyncio.wait_for(PostgresOutbox(worker).step(FIRST, bench.now, take), 5)
    another_row = await PostgresOutbox(worker).step(SECOND, bench.now, take)
    release.set()

    assert (await first, passed_over, another_row) == (True, False, True)
    assert [handoff.refund_id for handoff in seen_by_second] == [WITHOUT_TICKET]


async def test_two_workers_polling_together_submit_each_refund_once(
    bench: _Bench, worker: AsyncEngine
) -> None:
    other = bench.worker_on(PostgresOutbox(worker))

    held = await asyncio.gather(bench.poll_after(), other.poll())

    by_refund = sorted(s.refund_id for s in bench.provider.submissions)
    assert (sum(held), by_refund) == (2, [WITH_TICKET, WITHOUT_TICKET])
    assert [row["action"] for row in await bench.audited()] == ["submit", "submit"]


async def test_rows_are_due_oldest_first_and_only_once_their_time_has_come(bench: _Bench) -> None:
    outbox = bench.outbox

    assert await outbox.due(START - timedelta(seconds=1), 10) == []
    assert await outbox.due(START, 10) == [FIRST]
    assert await outbox.due(START + timedelta(seconds=1), 10) == [FIRST, SECOND]
    assert await outbox.due(START + timedelta(seconds=1), 1) == [FIRST]


async def test_a_row_that_is_finished_or_not_yet_due_is_not_held(bench: _Bench) -> None:
    async def never(_handoff: Handoff, _step: Step) -> None:
        raise AssertionError("the row should not have been held")

    await bench.change("UPDATE control.outbox SET status = 'done' WHERE refund_id = 301")

    assert await bench.outbox.step(FIRST, bench.now, never) is False
    assert await bench.outbox.step(SECOND, START, never) is False
    assert await bench.outbox.step(UUID(int=999), bench.now, never) is False


async def test_a_refund_that_is_not_where_the_step_expects_it_changes_nothing(
    bench: _Bench,
) -> None:
    await bench.change("UPDATE core.refunds SET status = 'failed', failure_reason = 'x'")

    await bench.poll_after()

    later = bench.now + timedelta(seconds=bench.limits.backoff_max_s)
    assert [(await bench.row(n))["status"] for n in (301, 302)] == ["pending", "pending"]
    assert [(await bench.row(n))["last_error"] for n in (301, 302)] == ["worker_error"] * 2
    assert await bench.audited() == []
    with pytest.raises(OutOfStepError):
        await bench.outbox.step(FIRST, later, _submit_as_if_approved)


async def _submit_as_if_approved(handoff: Handoff, step: Step) -> None:
    await step.submitted("sim_x", entry_for(handoff, "submit", 0.0, provider_ref="sim_x"))


async def test_a_finished_row_is_never_set_aside(bench: _Bench) -> None:
    await bench.change("UPDATE control.outbox SET status = 'done' WHERE refund_id = 301")

    await bench.outbox.set_aside(FIRST, bench.now + timedelta(days=1))
    await bench.outbox.set_aside(SECOND, bench.now + timedelta(days=1))

    done, waiting = await bench.row(301), await bench.row(302)
    assert (done["status"], done["last_error"], done["next_attempt_at"]) == ("done", None, START)
    assert (waiting["status"], waiting["last_error"]) == ("pending", "worker_error")
    assert waiting["next_attempt_at"] == bench.now + timedelta(days=1)


async def test_waiting_for_a_result_does_not_count_as_an_attempt(bench: _Bench) -> None:
    await bench.poll_after()

    await bench.poll_after()

    waiting = await bench.row()
    assert (waiting["status"], waiting["attempts"], waiting["last_error"]) == (
        "submitted",
        0,
        None,
    )
    assert waiting["next_attempt_at"] == bench.now + timedelta(seconds=bench.limits.poll_interval_s)


async def test_a_declined_refund_sent_round_again_is_paid_under_the_same_key(
    bench: _Bench,
) -> None:
    await bench.change("DELETE FROM control.outbox WHERE refund_id = :id", id=WITHOUT_TICKET)
    await bench.change("UPDATE control.chaos_flags SET enabled = true")
    drilled = bench.worker_on(bench.outbox, chaos_enabled=True)
    await bench.poll_after(handoffs=drilled)
    await bench.poll_after(3, handoffs=drilled)
    failed = await bench.refund()

    await bench.change("UPDATE core.refunds SET status = 'approved' WHERE id = 301")
    await bench.change(
        "INSERT INTO control.outbox (kind, refund_id, payload, next_attempt_at)"
        " VALUES ('refund.submit', 301, '{}'::jsonb, :now)",
        now=bench.now,
    )
    await bench.poll_after(handoffs=drilled)
    resent = await bench.refund()
    await bench.poll_after(3, handoffs=drilled)

    paid = await bench.refund()
    assert (failed["status"], failed["provider_ref"]) == ("failed", "sim_1")
    assert (resent["status"], resent["provider_ref"], resent["failure_reason"]) == (
        "sent_to_payments",
        "sim_2",
        None,
    )
    assert (paid["status"], paid["amount_minor"]) == ("paid", 62000)
    assert {s.idempotency_key for s in bench.provider.submissions} == {"refund-301"}
    assert [note["body"] for note in await bench.seen(_NOTES)] == [
        FOLLOW_UP,
        "Refund of INR 620.00 paid.",
    ]


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT * FROM core.customers",
        "SELECT * FROM core.orders",
        "SELECT * FROM core.tickets",
        "SELECT * FROM core.ticket_notes",
        "SELECT * FROM control.pending_actions",
        "SELECT * FROM audit.audit_log",
        "UPDATE core.refunds SET amount_minor = 1",
        "UPDATE core.refunds SET order_id = 102",
        "UPDATE core.refunds SET approved_by_name = 'x'",
        "INSERT INTO core.refunds (order_id, region, amount_minor, reason_code, reason_text,"
        " requested_by_oid, requested_by_name, approved_by_oid, approved_by_name,"
        " pending_action_id) SELECT order_id, region, 1, reason_code, 'x', requested_by_oid,"
        " 'x', approved_by_oid, 'x', gen_random_uuid() FROM core.refunds LIMIT 1",
        "DELETE FROM core.refunds",
        "INSERT INTO control.outbox (kind, refund_id, payload) VALUES"
        " ('refund.submit', 301, '{}'::jsonb)",
        "DELETE FROM control.outbox",
        "UPDATE control.chaos_flags SET name = 'x'",
        "INSERT INTO control.chaos_flags (name) VALUES ('x')",
    ],
)
async def test_the_workers_database_role_is_refused(worker: AsyncEngine, statement: str) -> None:
    async with worker.connect() as connection:
        with pytest.raises(DBAPIError, match="permission denied"):
            await connection.execute(text(statement))


async def test_the_worker_cannot_write_a_note_in_a_persons_name(worker: AsyncEngine) -> None:
    async with worker.connect() as connection:
        with pytest.raises(DBAPIError, match="row-level security"):
            await connection.execute(
                text(
                    "INSERT INTO core.ticket_notes (ticket_id, author_kind, author_name, body)"
                    " VALUES (201, 'engineer', 'Arjun Mehta', 'Approved by me.')"
                )
            )

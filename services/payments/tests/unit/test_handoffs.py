"""What the worker does with an approved refund, and with a provider that misbehaves."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
import structlog

from opscp_common.limits import Limits
from opscp_payments.ports.payments import Submission, Trouble
from opscp_payments.sim.store import Store
from opscp_payments.worker.outbox import Handoffs
from opscp_payments.worker.refunds import money, reason_from
from opscp_payments_testing.fakes import FakeOutbox, FakeProvider, Row

START = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
FIRST, SECOND = UUID(int=1), UUID(int=2)
REFUND = 5001
FOLLOW_UP = "Refund payment failed. Needs follow-up by an L2 engineer."


class _Bench:
    """A worker on in-memory backends, with one clock the test moves."""

    def __init__(self, limits: Limits, *rows: Row, chaos_enabled: bool = False) -> None:
        self.limits = limits.payments
        self.now = START
        self.spread = 1.0
        self.store = Store(
            settle_delay_s=limits.sim.settle_delay_s,
            capacity=limits.sim.refunds_kept,
            clock=lambda: (self.now - START).total_seconds(),
            new_ref=self._next_ref,
        )
        self._refs = 0
        self.outbox = FakeOutbox(*(rows or (Row(FIRST, REFUND, START),)))
        self.provider = FakeProvider(self.store)
        self.handoffs = Handoffs(
            self.outbox,
            self.provider,
            self.limits,
            lambda: self.now,
            chaos_enabled=chaos_enabled,
            spread=lambda: self.spread,
        )

    def _next_ref(self) -> str:
        self._refs += 1
        return f"sim_{self._refs}"

    async def poll_after(self, seconds: float = 0) -> int:
        self.now += timedelta(seconds=seconds)
        return await self.handoffs.poll()

    async def polls(self, count: int, *, every: float) -> None:
        for _ in range(count):
            await self.poll_after(every)

    @property
    def row(self) -> Row:
        return self.outbox.row(FIRST)

    def audited(self) -> list[tuple[str, str, str | None]]:
        return [(entry.action, entry.outcome, entry.error_code) for entry in self.outbox.audit]


@pytest.fixture
def bench(limits: Limits) -> _Bench:
    return _Bench(limits)


async def test_an_approved_refund_is_submitted_under_a_key_that_names_it(bench: _Bench) -> None:
    held = await bench.poll_after()

    (submission,) = bench.provider.submissions
    assert held == 1
    assert (submission.idempotency_key, submission.forced_outcome) == (f"refund-{REFUND}", None)
    assert (bench.row.refund_status, bench.row.provider_ref) == ("sent_to_payments", "sim_1")
    assert (bench.row.status, bench.row.attempts, bench.row.last_error) == ("submitted", 0, None)
    assert bench.audited() == [("submit", "ok", None)]
    assert bench.outbox.audit[0].model_dump() == {
        "trace_id": "trace-1",
        "target": "POST /refunds",
        "action": "submit",
        "args": {
            "refund_id": REFUND,
            "order_id": 4471,
            "outbox_id": str(FIRST),
            "attempt": 1,
            "provider_ref": "sim_1",
        },
        "outcome": "ok",
        "error_code": None,
        "duration_ms": bench.outbox.audit[0].duration_ms,
    }


async def test_a_refund_still_processing_is_asked_about_again_without_counting(
    bench: _Bench,
) -> None:
    await bench.poll_after()

    await bench.poll_after()
    waiting_until = bench.row.next_attempt_at
    nothing_due = await bench.poll_after(1)

    assert waiting_until == START + timedelta(seconds=bench.limits.poll_interval_s)
    assert nothing_due == 0
    assert (bench.row.status, bench.row.attempts, bench.row.refund_status) == (
        "submitted",
        0,
        "sent_to_payments",
    )
    assert bench.audited() == [("submit", "ok", None)]


async def test_a_refund_the_provider_paid_is_marked_paid_with_a_note_on_its_ticket(
    bench: _Bench,
) -> None:
    await bench.poll_after()

    await bench.poll_after(3)

    assert (bench.row.refund_status, bench.row.paid_at) == ("paid", START + timedelta(seconds=3))
    assert (bench.row.status, bench.row.failure_reason) == ("done", None)
    assert bench.outbox.notes == [(2207, "Refund of INR 620.00 paid.")]
    assert bench.audited() == [("submit", "ok", None), ("settle", "ok", None)]
    assert bench.outbox.audit[1].target == "GET /refunds/{provider_ref}"
    assert await bench.poll_after(60) == 0


async def test_a_refund_with_no_ticket_leaves_no_note(limits: Limits) -> None:
    bench = _Bench(limits, Row(FIRST, REFUND, START, ticket_id=None))

    await bench.poll_after()
    await bench.poll_after(3)

    assert (bench.row.refund_status, bench.outbox.notes) == ("paid", [])


async def test_a_declined_refund_fails_with_the_providers_reason_and_asks_for_follow_up(
    limits: Limits,
) -> None:
    bench = _Bench(limits, chaos_enabled=True)
    bench.outbox.forced_decline = True

    await bench.poll_after()
    await bench.poll_after(3)

    assert bench.provider.submissions[0].forced_outcome == "decline"
    assert (bench.row.refund_status, bench.row.failure_reason) == ("failed", "provider_declined")
    assert (bench.row.status, bench.row.paid_at) == ("failed", None)
    assert bench.outbox.notes == [(2207, FOLLOW_UP)]
    assert bench.audited() == [("submit", "ok", None), ("settle", "error", "PAYMENT_DECLINED")]
    assert bench.outbox.forced_decline is False


async def test_the_failure_drill_declines_one_refund_only(limits: Limits) -> None:
    rows = (Row(FIRST, REFUND, START), Row(SECOND, REFUND + 1, START + timedelta(seconds=1)))
    bench = _Bench(limits, *rows, chaos_enabled=True)
    bench.outbox.forced_decline = True

    await bench.poll_after(1)
    await bench.poll_after(3)

    assert [s.forced_outcome for s in bench.provider.submissions] == ["decline", None]
    assert (bench.row.refund_status, bench.outbox.row(SECOND).refund_status) == ("failed", "paid")


async def test_the_failure_drill_is_left_alone_unless_drills_are_enabled(bench: _Bench) -> None:
    bench.outbox.forced_decline = True

    await bench.poll_after()

    assert bench.provider.submissions[0].forced_outcome is None
    assert bench.outbox.forced_decline is True


@pytest.mark.parametrize("trouble", ["unreachable", "no_token"])
async def test_a_drill_whose_submission_cannot_have_arrived_is_put_back(
    limits: Limits, trouble: Trouble
) -> None:
    bench = _Bench(limits, chaos_enabled=True)
    bench.outbox.forced_decline = True
    bench.provider.troubles = [trouble]

    await bench.poll_after()

    assert (bench.outbox.forced_decline, bench.row.status) == (True, "pending")


@pytest.mark.parametrize(
    "trouble", ["timeout", "connection_lost", "server_error", "unexpected_answer"]
)
async def test_a_drill_the_provider_may_have_taken_is_not_handed_to_a_second_refund(
    limits: Limits, trouble: Trouble
) -> None:
    bench = _Bench(limits, chaos_enabled=True)
    bench.outbox.forced_decline = True
    bench.provider.troubles = [trouble]

    await bench.poll_after()

    assert (bench.outbox.forced_decline, bench.row.status) == (False, "pending")


@pytest.mark.parametrize(
    "trouble", ["timeout", "unreachable", "rate_limited", "server_error", "no_token"]
)
async def test_a_provider_that_gives_no_answer_is_tried_again_later(
    bench: _Bench, trouble: Trouble
) -> None:
    bench.provider.troubles = [trouble]

    await bench.poll_after()
    too_soon = await bench.poll_after(0.9)
    await bench.poll_after(0.1)

    assert too_soon == 0
    assert [s.idempotency_key for s in bench.provider.submissions] == [f"refund-{REFUND}"] * 2
    assert (bench.row.refund_status, bench.row.attempts, bench.row.last_error) == (
        "sent_to_payments",
        0,
        None,
    )
    assert bench.audited() == [("submit", "ok", None)]


@pytest.mark.parametrize(
    ("spread", "waits"), [(1.0, [1, 2, 4, 8]), (0.0, [0.5, 1, 2, 4])], ids=["longest", "shortest"]
)
async def test_each_retry_waits_about_twice_as_long_as_the_one_before(
    bench: _Bench, spread: float, waits: list[float]
) -> None:
    bench.spread = spread
    bench.provider.troubles = ["timeout"] * 4
    seen: list[float] = []

    for _ in waits:
        before = bench.now
        await bench.poll_after()
        seen.append((bench.row.next_attempt_at - before).total_seconds())
        bench.now = bench.row.next_attempt_at

    assert seen == waits


async def test_a_wait_never_exceeds_the_cap(limits: Limits) -> None:
    patient = limits.payments.model_copy(update={"max_attempts": 12})
    bench = _Bench(limits.model_copy(update={"payments": patient}))
    bench.provider.troubles = ["timeout"] * 9
    last_wait = timedelta(0)

    for _ in range(9):
        await bench.poll_after()
        last_wait = bench.row.next_attempt_at - bench.now
        bench.now = bench.row.next_attempt_at

    assert last_wait == timedelta(seconds=limits.payments.backoff_max_s)


async def test_after_the_last_attempt_the_refund_fails_as_unavailable(bench: _Bench) -> None:
    bench.provider.troubles = ["unreachable"] * bench.limits.max_attempts

    await bench.polls(bench.limits.max_attempts, every=bench.limits.backoff_max_s)

    assert len(bench.provider.submissions) == bench.limits.max_attempts
    assert (bench.row.refund_status, bench.row.failure_reason) == (
        "failed",
        "payment_service_unavailable",
    )
    assert (bench.row.status, bench.row.attempts, bench.row.last_error) == (
        "failed",
        bench.limits.max_attempts,
        "unreachable",
    )
    assert bench.outbox.notes == [(2207, FOLLOW_UP)]
    assert bench.audited() == [("submit", "error", "PAYMENT_SERVICE_UNAVAILABLE")]
    assert await bench.poll_after(3600) == 0


async def test_a_refund_whose_result_cannot_be_learned_fails_and_keeps_its_reference(
    bench: _Bench,
) -> None:
    await bench.poll_after()
    bench.provider.troubles = ["server_error"] * bench.limits.max_attempts

    await bench.polls(bench.limits.max_attempts, every=bench.limits.backoff_max_s)

    assert (bench.row.refund_status, bench.row.provider_ref) == ("failed", "sim_1")
    assert bench.row.failure_reason == "payment_service_unavailable"
    assert bench.audited()[-1] == ("settle", "error", "PAYMENT_SERVICE_UNAVAILABLE")
    assert bench.outbox.audit[-1].args["provider_ref"] == "sim_1"


async def test_a_crash_after_the_provider_answered_pays_the_refund_once(bench: _Bench) -> None:
    bench.outbox.crash_before_commit = True
    with structlog.testing.capture_logs() as logs:
        await bench.poll_after()
    unchanged = (bench.row.status, bench.row.refund_status, bench.row.provider_ref)
    set_aside_until = bench.row.next_attempt_at
    bench.outbox.crash_before_commit = False

    too_soon = await bench.poll_after(bench.limits.backoff_max_s - 1)
    await bench.poll_after(1)
    await bench.poll_after(3)

    assert unchanged == ("pending", "approved", None)
    assert (set_aside_until, too_soon) == (
        START + timedelta(seconds=bench.limits.backoff_max_s),
        0,
    )
    assert (bench.row.attempts, bench.row.last_error) == (0, None)
    assert [log["event"] for log in logs] == ["handoff_failed"]
    assert logs[0]["error"] == "CrashedBeforeCommitError"
    assert len(bench.provider.submissions) == 2
    assert (bench.row.provider_ref, bench.row.refund_status) == ("sim_1", "paid")
    assert bench.store.find("sim_2") is None


async def test_the_audit_line_is_logged_only_for_what_was_committed(bench: _Bench) -> None:
    bench.outbox.crash_before_commit = True
    with structlog.testing.capture_logs() as crashed:
        await bench.poll_after()
    bench.outbox.crash_before_commit = False
    with structlog.testing.capture_logs() as committed:
        await bench.poll_after(bench.limits.backoff_max_s)

    assert [log["event"] for log in crashed] == ["handoff_failed"]
    (line,) = committed
    assert (line["event"], line["audit_id"], line["action"], line["outcome"]) == (
        "audit",
        1,
        "submit",
        "ok",
    )


async def test_one_row_that_breaks_does_not_stop_the_next(limits: Limits) -> None:
    broken = Row(FIRST, REFUND, START, status="submitted", refund_status="sent_to_payments")
    bench = _Bench(limits, broken, Row(SECOND, REFUND + 1, START + timedelta(seconds=1)))

    with structlog.testing.capture_logs() as logs:
        held = await bench.poll_after(1)

    assert held == 1
    assert (logs[0]["event"], logs[0]["error"]) == ("handoff_failed", "RuntimeError")
    assert (bench.row.status, bench.outbox.row(SECOND).status) == ("submitted", "submitted")
    assert (bench.row.last_error, bench.row.attempts) == ("worker_error", 0)
    assert bench.row.next_attempt_at == bench.now + timedelta(seconds=bench.limits.backoff_max_s)


async def test_rows_that_always_break_do_not_keep_the_others_waiting(limits: Limits) -> None:
    """Taken oldest first, ten broken rows would otherwise fill every batch for ever."""
    broken = [
        Row(UUID(int=n), REFUND + n, START, status="submitted", refund_status="sent_to_payments")
        for n in range(1, limits.payments.claim_batch + 1)
    ]
    healthy = Row(UUID(int=99), REFUND + 99, START + timedelta(seconds=1))
    bench = _Bench(limits, *broken, healthy)

    with structlog.testing.capture_logs():
        first = await bench.poll_after(1)
        second = await bench.poll_after(1)

    assert (first, second) == (0, 1)
    assert bench.outbox.row(UUID(int=99)).status == "submitted"


async def test_a_row_that_cannot_even_be_set_aside_is_logged_and_left(
    bench: _Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def down(_outbox_id: UUID, _until: datetime) -> None:
        raise ConnectionError("postgres://payments_worker:secret@db is down")

    bench.outbox.crash_before_commit = True
    monkeypatch.setattr(bench.outbox, "set_aside", down)

    with structlog.testing.capture_logs() as logs:
        held = await bench.poll_after()

    assert held == 0
    assert [(log["event"], log["error"]) for log in logs] == [
        ("handoff_failed", "CrashedBeforeCommitError"),
        ("not_set_aside", "ConnectionError"),
    ]
    assert "secret" not in str(logs)


async def test_no_more_rows_are_taken_in_one_poll_than_the_batch_allows(limits: Limits) -> None:
    many = limits.payments.claim_batch + 3
    rows = [Row(UUID(int=n), REFUND + n, START + timedelta(seconds=n)) for n in range(1, many + 1)]
    bench = _Bench(limits, *rows)

    held = await bench.poll_after(many)

    assert held == limits.payments.claim_batch
    assert [s.refund_id for s in bench.provider.submissions] == [
        REFUND + n for n in range(1, limits.payments.claim_batch + 1)
    ]


async def test_what_the_provider_is_told_names_no_person() -> None:
    """SPEC 6.14.5: the payload carries no name, email or phone number."""
    assert set(Submission.model_fields) == {
        "refund_id",
        "order_id",
        "amount_minor",
        "currency",
        "forced_outcome",
    }


@pytest.mark.parametrize(
    ("amount_minor", "words"),
    [(62000, "INR 620.00"), (5, "INR 0.05"), (123456789, "INR 1,234,567.89"), (100, "INR 1.00")],
)
def test_money_is_written_with_two_decimal_places(amount_minor: int, words: str) -> None:
    assert money(amount_minor, "INR") == words


@pytest.mark.parametrize(
    ("said", "kept"),
    [
        ("provider_declined", "provider_declined"),
        ("insufficient_funds_2", "insufficient_funds_2"),
        (None, "declined"),
        ("", "declined"),
        ("Card closed by Asha Rao, call +91 90000 00001", "declined"),
        ("UPPER", "declined"),
        ("a" * 65, "declined"),
        ("ok\n", "declined"),
    ],
)
def test_only_a_plain_reason_code_from_the_provider_is_kept(said: str | None, kept: str) -> None:
    assert reason_from(said) == kept

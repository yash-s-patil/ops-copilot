"""The audit trail and the rate limiter against PostgreSQL."""

import asyncio
import runpy
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from opscp_common.redaction import redacted
from opscp_mcp.adapters.postgres.audit import PostgresAuditTrail, broken_links
from opscp_mcp.adapters.postgres.rate_limits import PostgresRateLimiter
from opscp_mcp.ports.controls import Allowance, AuditEntry

from .database import REPO_ROOT, WEST_ENGINEER, database_settings, guarded

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("fixture_rows")]

SCRIPT = REPO_ROOT / "scripts" / "verify-audit-chain.py"
PER_MINUTE = 60
BURST = 5
_LAST = text("SELECT * FROM audit.audit_log ORDER BY id DESC LIMIT 1")
_AGE = text(
    "UPDATE control.rate_limits SET refilled_at = refilled_at - make_interval(secs => :seconds) "
    "WHERE user_oid = :user_oid"
)
_BUCKET = text("SELECT tokens FROM control.rate_limits WHERE user_oid = :user_oid")


def _entry(**changed: object) -> AuditEntry:
    return AuditEntry.model_validate(
        {
            "actor_oid": WEST_ENGINEER,
            "actor_roles": ("Support.L1", "Support.L2"),
            "actor_kind": "user",
            "target": "lookup_customer",
            "action": "call",
            "args_redacted": {"email": "<email:0123456789abcdef>", "note": 'quote " and é'},
            "outcome": "ok",
            "result_hash": "a" * 64,
            "result_bytes": 512,
            "duration_ms": 12,
        }
        | changed
    )


async def test_an_entry_is_stored_as_it_was_given(mcp_app: AsyncEngine) -> None:
    entry = _entry()

    audit_id = await PostgresAuditTrail(guarded(mcp_app)).record(entry)

    async with mcp_app.connect() as connection:
        row = (await connection.execute(_LAST)).mappings().one()
    stored = {name: row[name] for name in entry.model_dump()}
    assert row["id"] == audit_id
    assert stored == entry.model_dump() | {"actor_roles": ["Support.L1", "Support.L2"]}
    assert row["written_by"] == "mcp_app"


async def test_arguments_made_safe_to_keep_can_always_be_stored(mcp_app: AsyncEngine) -> None:
    awkward = {"query": 'nul \x00 quote " backslash \\ emoji 😀 tab \t', "email": "a\x00@b.in"}
    kept = redacted(awkward, awkward.keys(), 200)

    await PostgresAuditTrail(guarded(mcp_app)).record(_entry(args_redacted=kept))

    async with mcp_app.connect() as connection:
        row = (await connection.execute(_LAST)).mappings().one()
    assert row["args_redacted"] == kept


async def test_a_denial_is_stored_with_no_result(mcp_app: AsyncEngine) -> None:
    await PostgresAuditTrail(guarded(mcp_app)).record(
        _entry(
            action="deny",
            outcome="denied",
            error_code="PERMISSION_DENIED",
            result_hash=None,
            result_bytes=None,
        )
    )

    async with mcp_app.connect() as connection:
        row = (await connection.execute(_LAST)).mappings().one()
    assert (row["action"], row["outcome"], row["error_code"], row["result_hash"]) == (
        "deny",
        "denied",
        "PERMISSION_DENIED",
        None,
    )


async def test_entries_written_at_the_same_moment_form_one_chain(mcp_app: AsyncEngine) -> None:
    trail = PostgresAuditTrail(guarded(mcp_app))
    async with mcp_app.connect() as connection:
        before, _ = await broken_links(connection, limit=1)

    await asyncio.gather(*(trail.record(_entry(duration_ms=number)) for number in range(25)))

    async with mcp_app.connect() as connection:
        after, broken = await broken_links(connection, limit=5)
    assert (after - before, broken) == (25, [])


@pytest.mark.parametrize(
    "tamper",
    [
        "UPDATE audit.audit_log SET outcome = 'denied' WHERE id = :id",
        "UPDATE audit.audit_log SET args_redacted = '{}' WHERE id = :id",
        "UPDATE audit.audit_log SET actor_oid = gen_random_uuid() WHERE id = :id",
        "UPDATE audit.audit_log SET occurred_at = occurred_at - interval '1 day' WHERE id = :id",
        "DELETE FROM audit.audit_log WHERE id = :id",
    ],
    ids=["outcome", "arguments", "actor", "time", "row removed"],
)
async def test_a_changed_or_removed_row_is_found(
    mcp_app: AsyncEngine, migrator: AsyncEngine, tamper: str
) -> None:
    trail = PostgresAuditTrail(guarded(mcp_app))
    for number in range(3):
        await trail.record(_entry(duration_ms=number))

    async with migrator.connect() as connection:
        transaction = await connection.begin()
        last = (await connection.execute(text("SELECT max(id) FROM audit.audit_log"))).scalar_one()
        await connection.execute(text("ALTER TABLE audit.audit_log DISABLE TRIGGER USER"))
        await connection.execute(text(tamper), {"id": last - 1})
        _, broken = await broken_links(connection, limit=5)
        await transaction.rollback()

    async with mcp_app.connect() as connection:
        _, restored = await broken_links(connection, limit=5)
    assert broken in ([last - 1], [last])
    assert restored == []


@pytest.fixture
def database_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = database_settings("mcp_app")
    for name, value in {
        "DATABASE_HOST": settings.host,
        "DATABASE_PORT": str(settings.port),
        "DATABASE_NAME": settings.name,
        "DATABASE_USER": settings.user,
        "DATABASE_PASSWORD": settings.password.get_secret_value(),
    }.items():
        monkeypatch.setenv(name, value)


async def test_the_script_says_so_when_the_log_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    unreachable = {"HOST": "127.0.0.1", "PORT": "1", "NAME": "x", "USER": "x", "PASSWORD": "x"}
    for name, value in unreachable.items():
        monkeypatch.setenv(f"DATABASE_{name}", value)

    status = await runpy.run_path(str(SCRIPT))["main"]()

    assert status == 2
    assert "127.0.0.1" not in capsys.readouterr().err


@pytest.mark.usefixtures("database_environment")
async def test_the_script_reports_a_whole_chain(
    mcp_app: AsyncEngine, capsys: pytest.CaptureFixture[str]
) -> None:
    await PostgresAuditTrail(guarded(mcp_app)).record(_entry())

    status = await runpy.run_path(str(SCRIPT))["main"]()

    assert status == 0
    assert "The audit chain is whole:" in capsys.readouterr().out


@pytest.mark.usefixtures("database_environment")
async def test_the_script_names_the_rows_of_a_broken_chain(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = runpy.run_path(str(SCRIPT))

    async def tampered(_connection: object, *, limit: int) -> tuple[int, list[int]]:
        return 9, [4, 5][:limit]

    monkeypatch.setitem(script["main"].__globals__, "broken_links", tampered)

    assert await script["main"]() == 1
    assert "First rows that fail: 4, 5." in capsys.readouterr().err


def _limiter(engine: AsyncEngine) -> PostgresRateLimiter:
    return PostgresRateLimiter(guarded(engine), per_minute=PER_MINUTE, burst=BURST)


async def _take(limiter: PostgresRateLimiter, user: UUID, times: int) -> list[Allowance]:
    return [await limiter.take(user) for _ in range(times)]


async def test_a_new_user_gets_the_burst_and_then_must_wait(mcp_app: AsyncEngine) -> None:
    taken = await _take(_limiter(mcp_app), uuid4(), BURST + 2)

    assert [allowance.allowed for allowance in taken] == [True] * BURST + [False, False]
    assert {allowance.retry_after_s for allowance in taken[BURST:]} == {1}


async def test_the_allowance_refills_with_time_up_to_the_burst(
    mcp_app: AsyncEngine, migrator: AsyncEngine
) -> None:
    limiter, user = _limiter(mcp_app), uuid4()
    await _take(limiter, user, BURST + 1)

    async with migrator.begin() as connection:
        await connection.execute(_AGE, {"seconds": 2.5, "user_oid": user})
    after_a_while = await _take(limiter, user, 3)
    async with migrator.begin() as connection:
        await connection.execute(_AGE, {"seconds": 86_400, "user_oid": user})
    after_a_day = await _take(limiter, user, BURST + 1)

    assert [allowance.allowed for allowance in after_a_while] == [True, True, False]
    assert [allowance.allowed for allowance in after_a_day] == [True] * BURST + [False]


async def test_requests_at_the_same_moment_cannot_exceed_the_burst(mcp_app: AsyncEngine) -> None:
    limiter, user = _limiter(mcp_app), uuid4()

    taken = await asyncio.gather(*(limiter.take(user) for _ in range(BURST * 6)))

    assert sum(allowance.allowed for allowance in taken) == BURST
    async with mcp_app.connect() as connection:
        tokens = (await connection.execute(_BUCKET, {"user_oid": user})).scalar_one()
    assert 0 <= tokens < 1


async def test_users_are_counted_separately(mcp_app: AsyncEngine) -> None:
    limiter, first, second = _limiter(mcp_app), uuid4(), uuid4()
    await _take(limiter, first, BURST)

    assert [(await limiter.take(first)).allowed, (await limiter.take(second)).allowed] == [
        False,
        True,
    ]


async def test_a_slow_user_is_told_how_long_one_call_takes_to_come_back(
    mcp_app: AsyncEngine,
) -> None:
    slow, user = PostgresRateLimiter(guarded(mcp_app), per_minute=2, burst=1), uuid4()

    _, refused = await slow.take(user), await slow.take(user)

    assert (refused.allowed, refused.retry_after_s) == (False, 30)


async def test_a_clock_that_ran_ahead_does_not_take_allowance_away(
    mcp_app: AsyncEngine, migrator: AsyncEngine
) -> None:
    limiter, user = _limiter(mcp_app), uuid4()
    await limiter.take(user)
    async with migrator.begin() as connection:
        await connection.execute(_AGE, {"seconds": -3600, "user_oid": user})

    taken = await _take(limiter, user, BURST)

    assert [allowance.allowed for allowance in taken] == [True] * (BURST - 1) + [False]

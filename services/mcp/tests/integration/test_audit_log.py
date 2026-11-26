import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from .database import WEST_ENGINEER, transaction

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("fixture_rows")]

_GENESIS_HASH = "0" * 64

_APPEND = text(
    """
    SELECT audit.append(
        :trace_id, :run_id, :actor_oid, :actor_roles, 'user', :target, 'call',
        CAST(:args AS jsonb), 'ok', NULL, :result_hash, 236, 212
    )
    """
)
_CHAIN = text("SELECT id, prev_hash, row_hash FROM audit.audit_log ORDER BY id")
_RECOMPUTED = text(
    """
    SELECT count(*) FROM audit.audit_log
    WHERE row_hash <> audit.compute_row_hash(
        prev_hash, written_by, occurred_at, trace_id, run_id, actor_oid, actor_roles,
        actor_kind, target,
        action, args_redacted, outcome, error_code, result_hash, result_bytes, duration_ms
    )
    """
)


async def _append(connection: AsyncConnection, target: str) -> int:
    result = await connection.execute(
        _APPEND,
        {
            "trace_id": "trace-1",
            "run_id": "run-1",
            "actor_oid": WEST_ENGINEER,
            "actor_roles": ["Support.L2"],
            "target": target,
            "args": '{"order_id": 4471}',
            "result_hash": "abc",
        },
    )
    return result.scalar_one()


async def test_each_row_links_to_the_row_before_it(mcp_app: AsyncEngine) -> None:
    async with transaction(mcp_app, region="west") as connection:
        for target in ("get_order", "lookup_customer", "get_ticket"):
            await _append(connection, target)
        rows = (await connection.execute(_CHAIN)).all()

    assert rows[0].prev_hash == _GENESIS_HASH
    assert [row.prev_hash for row in rows[1:]] == [row.row_hash for row in rows[:-1]]
    assert len({row.row_hash for row in rows}) == len(rows)


async def test_stored_hashes_match_a_recomputation(mcp_app: AsyncEngine) -> None:
    async with transaction(mcp_app, region="west") as connection:
        await _append(connection, "get_order")
        await _append(connection, "get_order")

        assert (await connection.execute(_RECOMPUTED)).scalar_one() == 0


async def test_payments_worker_can_append(payments_worker: AsyncEngine) -> None:
    async with transaction(payments_worker) as connection:
        assert await _append(connection, "payments.submit") > 0


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit.audit_log SET outcome = 'denied'",
        "DELETE FROM audit.audit_log",
        "TRUNCATE audit.audit_log",
    ],
)
async def test_table_owner_cannot_rewrite_history(
    mcp_app: AsyncEngine, migrator: AsyncEngine, statement: str
) -> None:
    async with mcp_app.begin() as connection:
        await _append(connection, "get_order")

    async with transaction(migrator, region="*") as connection:
        with pytest.raises(DBAPIError, match="append-only"):
            await connection.execute(text(statement))


@pytest.mark.parametrize(
    ("field", "value"),
    [("actor_kind", "robot"), ("action", "erase"), ("outcome", "maybe")],
)
async def test_unknown_enumeration_value_is_rejected(
    mcp_app: AsyncEngine, field: str, value: str
) -> None:
    values = {"actor_kind": "user", "action": "call", "outcome": "ok"} | {field: value}
    statement = text(
        """
        SELECT audit.append(
            NULL, NULL, NULL, '{}', :actor_kind, 'get_order', :action, '{}'::jsonb,
            :outcome, NULL, NULL, NULL, NULL
        )
        """
    )

    async with transaction(mcp_app, region="west") as connection:
        with pytest.raises(DBAPIError, match="violates check constraint"):
            await connection.execute(statement, values)


async def test_two_rows_cannot_share_a_predecessor(
    mcp_app: AsyncEngine, migrator: AsyncEngine
) -> None:
    async with mcp_app.begin() as connection:
        await _append(connection, "get_order")

    fork = text(
        """
        INSERT INTO audit.audit_log
            (occurred_at, written_by, actor_roles, actor_kind, target, action, args_redacted,
             outcome, prev_hash, row_hash)
        SELECT now(), 'forged', '{}', 'user', 'forged', 'call', '{}'::jsonb, 'ok', prev_hash,
               'forged'
        FROM audit.audit_log ORDER BY id DESC LIMIT 1
        """
    )
    async with transaction(migrator, region="*") as connection:
        with pytest.raises(DBAPIError, match="audit_log_prev_hash_key"):
            await connection.execute(fork)


async def test_timestamps_follow_chain_order(mcp_app: AsyncEngine) -> None:
    async with transaction(mcp_app, region="west") as connection:
        for _ in range(5):
            await _append(connection, "get_order")
        out_of_order = await connection.execute(
            text(
                "SELECT count(*) FROM (SELECT occurred_at, lag(occurred_at) OVER (ORDER BY id) "
                "AS previous FROM audit.audit_log) AS rows WHERE occurred_at < previous"
            )
        )
        assert out_of_order.scalar_one() == 0


async def test_mcp_app_rows_are_attributed_to_it(mcp_app: AsyncEngine) -> None:
    async with transaction(mcp_app, region="west") as connection:
        row_id = await _append(connection, "get_order")
        written_by = await connection.execute(
            text("SELECT written_by FROM audit.audit_log WHERE id = :id"), {"id": row_id}
        )
        assert written_by.scalar_one() == "mcp_app"


async def test_payments_worker_rows_are_attributed_to_it(
    payments_worker: AsyncEngine, mcp_app: AsyncEngine
) -> None:
    async with payments_worker.begin() as connection:
        row_id = await _append(connection, "payments.submit")

    async with transaction(mcp_app, region="west") as connection:
        written_by = await connection.execute(
            text("SELECT written_by FROM audit.audit_log WHERE id = :id"), {"id": row_id}
        )
        assert written_by.scalar_one() == "payments_worker"


@pytest.mark.parametrize(
    ("role", "function"),
    [
        (
            "payments_worker",
            "audit.compute_row_hash(text, text, timestamptz, text, text, uuid, "
            "text[], text, text, text, jsonb, text, text, text, integer, integer)",
        ),
        ("mcp_app", "audit.reject_change()"),
        ("payments_worker", "audit.reject_change()"),
    ],
)
async def test_functions_are_not_executable_unless_granted(
    migrator: AsyncEngine, role: str, function: str
) -> None:
    async with transaction(migrator, region="*") as connection:
        allowed = await connection.execute(
            text("SELECT has_function_privilege(:role, :function, 'EXECUTE')"),
            {"role": role, "function": function},
        )
        assert allowed.scalar_one() is False

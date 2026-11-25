import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from .database import PENDING_ACTION, WEST, WEST_ENGINEER, database_settings, transaction

pytestmark = pytest.mark.integration

_DENIED = "permission denied"

_MCP_APP_REFUSALS = {
    "delete a ticket": "DELETE FROM core.tickets WHERE id = 201",
    "delete a refund": "DELETE FROM core.refunds WHERE id = 301",
    "delete a note": "DELETE FROM core.ticket_notes WHERE ticket_id = 201",
    "create a customer": (
        "INSERT INTO core.customers (full_name, email, tier, region) "
        "VALUES ('X', 'x@example.in', 'standard', 'west')"
    ),
    "change a customer": "UPDATE core.customers SET tier = 'premium' WHERE id = 1",
    "change an order": "UPDATE core.orders SET amount_minor = 1 WHERE id = 101",
    "create an order event": (
        "INSERT INTO core.order_events (order_id, event_type, occurred_at) "
        "VALUES (101, 'delivered', now())"
    ),
    "create a ticket": (
        "INSERT INTO core.tickets (order_id, customer_id, region, priority, summary) "
        "VALUES (101, 1, 'west', 'P3', 'x')"
    ),
    "move a ticket to another region": "UPDATE core.tickets SET region = 'south' WHERE id = 201",
    "point a ticket at another order": "UPDATE core.tickets SET order_id = 102 WHERE id = 201",
    "point a ticket at another customer": "UPDATE core.tickets SET customer_id = 2",
    "rewrite a ticket summary": "UPDATE core.tickets SET summary = 'x' WHERE id = 201",
    "change a note": "UPDATE core.ticket_notes SET body = 'x'",
    "change a refund amount": "UPDATE core.refunds SET amount_minor = 1 WHERE id = 301",
    "change who approved a refund": "UPDATE core.refunds SET approved_by_name = 'x'",
    "record a payment result": "UPDATE core.refunds SET provider_ref = 'x', paid_at = now()",
    "rewrite a used approval token": (
        "UPDATE control.approval_token_uses SET jti = gen_random_uuid()"
    ),
    "change an outbox row": "UPDATE control.outbox SET status = 'done'",
    "delete a pending action": "DELETE FROM control.pending_actions",
    "delete a used approval token": "DELETE FROM control.approval_token_uses",
    "delete an outbox row": "DELETE FROM control.outbox",
    "write an audit row directly": (
        "INSERT INTO audit.audit_log "
        "(occurred_at, written_by, actor_roles, actor_kind, target, action, "
        "args_redacted, outcome, prev_hash, row_hash) "
        "VALUES (now(), 'x', '{}', 'user', 't', 'call', '{}', 'ok', 'p', 'r')"
    ),
    "change an audit row": "UPDATE audit.audit_log SET outcome = 'ok'",
    "delete an audit row": "DELETE FROM audit.audit_log",
    "empty the audit log": "TRUNCATE audit.audit_log",
    "empty a business table": "TRUNCATE core.tickets CASCADE",
    "drop a table": "DROP TABLE core.ticket_notes",
    "disable row-level security": "ALTER TABLE core.orders DISABLE ROW LEVEL SECURITY",
    "create a table": "CREATE TABLE core.scratch (id int)",
}

_PAYMENTS_WORKER_REFUSALS = {
    "read customers": "SELECT 1 FROM core.customers",
    "read orders": "SELECT 1 FROM core.orders",
    "read order events": "SELECT 1 FROM core.order_events",
    "read tickets": "SELECT 1 FROM core.tickets",
    "read ticket notes": "SELECT 1 FROM core.ticket_notes",
    "read pending actions": "SELECT 1 FROM control.pending_actions",
    "read used approval tokens": "SELECT 1 FROM control.approval_token_uses",
    "read rate limits": "SELECT 1 FROM control.rate_limits",
    "read the audit log": "SELECT 1 FROM audit.audit_log",
    "create a refund": (
        "INSERT INTO core.refunds (order_id, region, amount_minor, reason_code, reason_text, "
        "requested_by_oid, requested_by_name, approved_by_oid, approved_by_name, "
        "pending_action_id) VALUES (101, 'west', 1, 'damaged', 'x', gen_random_uuid(), 'x', "
        "gen_random_uuid(), 'x', gen_random_uuid())"
    ),
    "change a refund amount": "UPDATE core.refunds SET amount_minor = 1 WHERE id = 301",
    "change a refund reason": "UPDATE core.refunds SET reason_code = 'damaged' WHERE id = 301",
    "change who approved a refund": "UPDATE core.refunds SET approved_by_name = 'x'",
    "move a refund to another order": "UPDATE core.refunds SET order_id = 102 WHERE id = 301",
    "delete a refund": "DELETE FROM core.refunds WHERE id = 301",
    "create an outbox row": (
        "INSERT INTO control.outbox (kind, refund_id, payload) VALUES ('refund.submit', 301, '{}')"
    ),
    "delete an outbox row": "DELETE FROM control.outbox",
    "rename a chaos flag": "UPDATE control.chaos_flags SET name = 'x'",
    "change a chaos flag value": "UPDATE control.chaos_flags SET value = '{}'",
    "create a chaos flag": "INSERT INTO control.chaos_flags (name) VALUES ('x')",
    "change a ticket": "UPDATE core.tickets SET status = 'resolved'",
    "write an audit row directly": (
        "INSERT INTO audit.audit_log "
        "(occurred_at, written_by, actor_roles, actor_kind, target, action, "
        "args_redacted, outcome, prev_hash, row_hash) "
        "VALUES (now(), 'x', '{}', 'service', 't', 'submit', '{}', 'ok', 'p', 'r')"
    ),
}

_SYSTEM_NOTE = text(
    "INSERT INTO core.ticket_notes (ticket_id, author_kind, author_name, body) "
    "VALUES (:ticket, :kind, 'Payments', 'Refund of INR 620.00 paid.')"
)


@pytest.mark.parametrize("statement", _MCP_APP_REFUSALS.values(), ids=_MCP_APP_REFUSALS.keys())
async def test_mcp_app_cannot(mcp_app: AsyncEngine, statement: str) -> None:
    async with transaction(mcp_app, region="*") as connection:
        with pytest.raises(DBAPIError, match=f"{_DENIED}|must be owner|append-only"):
            await connection.execute(text(statement))


@pytest.mark.parametrize(
    "statement", _PAYMENTS_WORKER_REFUSALS.values(), ids=_PAYMENTS_WORKER_REFUSALS.keys()
)
async def test_payments_worker_cannot(payments_worker: AsyncEngine, statement: str) -> None:
    async with transaction(payments_worker) as connection:
        with pytest.raises(DBAPIError, match=_DENIED):
            await connection.execute(text(statement))


async def test_payments_worker_can_move_a_refund_through_its_payment_states(
    payments_worker: AsyncEngine,
) -> None:
    async with transaction(payments_worker) as connection:
        result = await connection.execute(
            text(
                "UPDATE core.refunds SET status = 'paid', provider_ref = 'sim-1', "
                "paid_at = now(), failure_reason = NULL, updated_at = now() WHERE id = :id"
            ),
            {"id": WEST["refund"]},
        )
        assert result.rowcount == 1


async def test_payments_worker_can_claim_and_update_outbox_rows(
    payments_worker: AsyncEngine,
) -> None:
    async with transaction(payments_worker) as connection:
        claimed = await connection.execute(
            text(
                "SELECT id FROM control.outbox WHERE status = 'pending' "
                "ORDER BY created_at FOR UPDATE SKIP LOCKED"
            )
        )
        outbox_id = claimed.scalar_one()
        result = await connection.execute(
            text(
                "UPDATE control.outbox SET status = 'submitted', attempts = attempts + 1 "
                "WHERE id = :id"
            ),
            {"id": outbox_id},
        )
        assert result.rowcount == 1


async def test_payments_worker_can_clear_a_chaos_flag(payments_worker: AsyncEngine) -> None:
    async with transaction(payments_worker) as connection:
        result = await connection.execute(
            text("UPDATE control.chaos_flags SET enabled = false WHERE name = 'payment_fail'")
        )
        assert result.rowcount == 1


async def test_payments_worker_can_add_a_system_note(payments_worker: AsyncEngine) -> None:
    async with transaction(payments_worker) as connection:
        result = await connection.execute(
            _SYSTEM_NOTE, {"ticket": WEST["ticket"], "kind": "system"}
        )
        assert result.rowcount == 1


@pytest.mark.parametrize("kind", ["engineer", "customer", "agent_on_behalf"])
async def test_payments_worker_cannot_write_a_note_as_anyone_else(
    payments_worker: AsyncEngine, kind: str
) -> None:
    async with transaction(payments_worker) as connection:
        with pytest.raises(DBAPIError, match="row-level security"):
            await connection.execute(_SYSTEM_NOTE, {"ticket": WEST["ticket"], "kind": kind})


async def test_agent_role_cannot_connect_to_the_business_database(migrator: AsyncEngine) -> None:
    engine = create_async_engine(database_settings("agent_app").url())
    try:
        with pytest.raises(DBAPIError, match="permission denied for database"):
            async with engine.connect():
                pass
    finally:
        await engine.dispose()


async def test_mcp_app_can_escalate_a_ticket(mcp_app: AsyncEngine) -> None:
    async with transaction(mcp_app, region="west") as connection:
        result = await connection.execute(
            text(
                "UPDATE core.tickets SET queue = 'L2', escalated_by_oid = gen_random_uuid(), "
                "escalated_by_name = 'West Engineer', escalated_at = now(), "
                "version = version + 1, updated_at = now() WHERE id = :id AND version = 1"
            ),
            {"id": WEST["ticket"]},
        )
        assert result.rowcount == 1


async def test_mcp_app_can_send_a_failed_refund_round_again(mcp_app: AsyncEngine) -> None:
    async with transaction(mcp_app, region="west") as connection:
        result = await connection.execute(
            text("UPDATE core.refunds SET status = 'approved', updated_at = now() WHERE id = :id"),
            {"id": WEST["refund"]},
        )
        assert result.rowcount == 1


async def test_approval_token_can_be_recorded_only_once(mcp_app: AsyncEngine) -> None:
    record = text(
        "INSERT INTO control.approval_token_uses (jti, pending_action_id) "
        "VALUES (:jti, :pending_action_id)"
    )
    values = {"jti": WEST_ENGINEER, "pending_action_id": PENDING_ACTION}

    async with transaction(mcp_app, region="west") as connection:
        await connection.execute(record, values)
        with pytest.raises(DBAPIError, match="approval_token_uses_pkey"):
            await connection.execute(record, values)

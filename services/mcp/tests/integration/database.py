from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from opscp_mcp.settings import DatabaseSettings

REPO_ROOT = Path(__file__).resolve().parents[4]
SERVICE_ROOT = Path(__file__).resolve().parents[2]
TEST_DATABASE = "meridian_test"

WEST_ENGINEER = UUID("11111111-1111-4111-8111-111111111111")
SOUTH_ENGINEER = UUID("22222222-2222-4222-8222-222222222222")
PENDING_ACTION = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")

WEST = {"customer": 1, "order": 101, "ticket": 201, "refund": 301}
SOUTH = {"order": 102, "ticket": 202}

SET_USER_CONTEXT = text(
    "SELECT set_config('app.user_oid', :oid, true), set_config('app.region', :region, true)"
)

FIXTURE_PARAMETERS = {"west_engineer": WEST_ENGINEER, "pending_action": PENDING_ACTION}

FIXTURE_ROWS = (
    """
    INSERT INTO core.customers (id, full_name, email, phone, tier, region) VALUES
        (1, 'Asha West', 'asha.west@example.in', '+91 90000 00001', 'premium', 'west'),
        (2, 'Bala South', 'bala.south@example.in', '+91 90000 00002', 'standard', 'south')
    """,
    """
    INSERT INTO core.orders
        (id, customer_id, status, amount_minor, region, placed_at, status_changed_at)
    VALUES
        (101, 1, 'packed', 620000, 'west', now() - interval '5 days', now() - interval '4 days'),
        (102, 2, 'packed', 290000, 'south', now() - interval '3 days', now() - interval '2 days')
    """,
    """
    INSERT INTO core.order_events (order_id, event_type, occurred_at) VALUES
        (101, 'packed', now() - interval '4 days'),
        (102, 'packed', now() - interval '2 days')
    """,
    """
    INSERT INTO core.tickets (id, order_id, customer_id, region, priority, summary) VALUES
        (201, 101, 1, 'west', 'P2', 'West order is stuck'),
        (202, 102, 2, 'south', 'P3', 'South order is stuck')
    """,
    """
    INSERT INTO core.ticket_notes (ticket_id, author_kind, author_name, body) VALUES
        (201, 'customer', 'Asha West', 'Where is my order?'),
        (202, 'customer', 'Bala South', 'Where is my order?')
    """,
    """
    INSERT INTO control.pending_actions
        (id, tool, args, args_hash, requester_oid, requester_name, requester_region,
         approval_kind, status, approver_oid, approver_name, decided_at, executed_at,
         idempotency_key, ticket_id, expires_at)
    VALUES
        (:pending_action, 'issue_refund', '{}'::jsonb, 'hash',
         :west_engineer, 'West Engineer', 'west', 'self', 'executed',
         :west_engineer, 'West Engineer', now(), now(),
         'fixture-key', 201, now() + interval '1 hour')
    """,
    """
    INSERT INTO core.refunds
        (id, order_id, ticket_id, region, amount_minor, reason_code, reason_text,
         requested_by_oid, requested_by_name, approved_by_oid, approved_by_name,
         pending_action_id)
    VALUES
        (301, 101, 201, 'west', 62000, 'goodwill_delay', 'Delayed 96 hours',
         :west_engineer, 'West Engineer',
         :west_engineer, 'West Engineer',
         :pending_action)
    """,
    """
    INSERT INTO control.outbox (kind, refund_id, payload)
    VALUES ('refund.submit', 301, '{"amount_minor": 62000}'::jsonb)
    """,
    "INSERT INTO control.chaos_flags (name) VALUES ('payment_fail')",
)


def database_settings(user: str, database: str = TEST_DATABASE) -> DatabaseSettings:
    base = DatabaseSettings(_env_file=REPO_ROOT / ".env")  # pyright: ignore[reportCallIssue] -- fields come from the env file
    return base.model_copy(update={"user": user, "name": database})


@asynccontextmanager
async def transaction(
    engine: AsyncEngine, *, region: str | None = None, user_oid: UUID = WEST_ENGINEER
) -> AsyncGenerator[AsyncConnection]:
    """One transaction that is always rolled back. `region=None` sets no user context."""
    async with engine.connect() as connection:
        outer = await connection.begin()
        try:
            if region is not None:
                await connection.execute(SET_USER_CONTEXT, {"oid": str(user_oid), "region": region})
            yield connection
        finally:
            await outer.rollback()

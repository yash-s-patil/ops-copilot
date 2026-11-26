"""Puts the shared test world into the test database, and takes it out again."""

from dataclasses import asdict
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncConnection

from opscp_common.vocabulary import ALL_REGIONS
from opscp_mcp_testing.world import World

from .database import SET_USER_CONTEXT, TEST_DATABASE

_FIRST_WORLD_ID = 900000
_REQUESTER = UUID("90000000-0000-4000-8000-0000000000ff")

_INSERT_CUSTOMER = text(
    "INSERT INTO core.customers (id, full_name, email, phone, tier, region, created_at)"
    " VALUES (:id, :full_name, :email, :phone, :tier, :region, :created_at)"
)
_INSERT_ORDER = text(
    "INSERT INTO core.orders (id, customer_id, status, amount_minor, currency, region, carrier,"
    " tracking_ref, placed_at, status_changed_at) VALUES (:id, :customer_id, :status,"
    " :amount_minor, :currency, :region, :carrier, :tracking_ref, :placed_at, :status_changed_at)"
)
_INSERT_EVENT = text(
    "INSERT INTO core.order_events (order_id, event_type, payload, occurred_at)"
    " VALUES (:order_id, :event_type, :payload, :occurred_at)"
).bindparams(bindparam("payload", type_=JSONB))
_INSERT_TICKET = text(
    "INSERT INTO core.tickets (id, order_id, customer_id, region, status, priority, summary)"
    " VALUES (:id, :order_id, :customer_id, :region, :status, 'P3', 'A test ticket')"
)
_INSERT_PENDING_ACTION = text(
    "INSERT INTO control.pending_actions (id, tool, args, args_hash, requester_oid,"
    " requester_name, requester_region, approval_kind, status, executed_at, idempotency_key,"
    " expires_at) VALUES (:pending_action_id, 'issue_refund', '{}'::jsonb, 'hash', :requester,"
    " 'Test Engineer', :region, 'self', 'executed', now(), :idempotency_key, now())"
)
_INSERT_REFUND = text(
    "INSERT INTO core.refunds (id, order_id, region, amount_minor, reason_code, reason_text,"
    " status, failure_reason, requested_by_oid, requested_by_name, approved_by_oid,"
    " approved_by_name, pending_action_id) VALUES (:id, :order_id, :region, :amount_minor,"
    " 'goodwill_delay', 'Delayed', :status, 'declined', :requester, 'Test Engineer', :requester,"
    " 'Test Engineer', :pending_action_id)"
)
_DELETES = tuple(
    text(statement)
    for statement in (
        "DELETE FROM core.refunds WHERE id >= :first",
        "DELETE FROM control.pending_actions WHERE requester_oid = :requester",
        "DELETE FROM core.tickets WHERE id >= :first",
        "DELETE FROM core.order_events WHERE order_id >= :first",
        "DELETE FROM core.orders WHERE id >= :first",
        "DELETE FROM core.customers WHERE id >= :first",
    )
)


async def _as_owner_of_the_test_database(connection: AsyncConnection) -> None:
    database = await connection.scalar(text("SELECT current_database()"))
    if database != TEST_DATABASE:
        raise RuntimeError(f"refusing to change test rows in {database}")
    await connection.execute(SET_USER_CONTEXT, {"oid": str(_REQUESTER), "region": ALL_REGIONS})


async def load_world(connection: AsyncConnection, world: World) -> None:
    await _as_owner_of_the_test_database(connection)
    await connection.execute(_INSERT_CUSTOMER, [asdict(row) for row in world.customers])
    await connection.execute(_INSERT_ORDER, [asdict(row) for row in world.orders])
    await connection.execute(_INSERT_EVENT, [asdict(row) for row in world.events])
    await connection.execute(_INSERT_TICKET, [asdict(row) for row in world.tickets])
    refunds = [
        {**asdict(row), "idempotency_key": str(row.pending_action_id), "requester": _REQUESTER}
        for row in world.refunds
    ]
    await connection.execute(_INSERT_PENDING_ACTION, refunds)
    await connection.execute(_INSERT_REFUND, refunds)


async def remove_world(connection: AsyncConnection) -> None:
    await _as_owner_of_the_test_database(connection)
    for statement in _DELETES:
        parameters = {"first": _FIRST_WORLD_ID, "requester": _REQUESTER}
        await connection.execute(
            statement,
            {key: value for key, value in parameters.items() if f":{key}" in statement.text},
        )

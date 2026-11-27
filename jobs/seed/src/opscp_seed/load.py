"""Replaces the contents of the meridian database with a dataset, in the caller's transaction."""

from collections.abc import Sequence
from dataclasses import asdict
from typing import Any

from sqlalchemy import TextClause, bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncConnection

from opscp_seed.model import Dataset

ALL_REGIONS = "*"
FIRST_ID_AFTER_SEED = 5001

_SET_REGION = text("SELECT set_config('app.region', :region, true)")

_EMPTY_EVERY_TABLE = text(
    """
    TRUNCATE
        core.customers, core.orders, core.order_events, core.tickets, core.ticket_notes,
        core.refunds, control.pending_actions, control.approval_token_uses, control.outbox,
        control.rate_limits
    RESTART IDENTITY
    """
)

_INSERT_CUSTOMERS = text(
    """
    INSERT INTO core.customers (id, full_name, email, phone, tier, region, created_at)
    VALUES (:id, :full_name, :email, :phone, :tier, :region, :created_at)
    """
)

_INSERT_ORDERS = text(
    """
    INSERT INTO core.orders
        (id, customer_id, status, amount_minor, region, carrier, tracking_ref,
         placed_at, status_changed_at)
    VALUES
        (:id, :customer_id, :status, :amount_minor, :region, :carrier, :tracking_ref,
         :placed_at, :status_changed_at)
    """
)

_INSERT_EVENTS = text(
    """
    INSERT INTO core.order_events (order_id, event_type, payload, occurred_at)
    VALUES (:order_id, :event_type, :payload, :occurred_at)
    """
).bindparams(bindparam("payload", type_=JSONB))

_INSERT_TICKETS = text(
    """
    INSERT INTO core.tickets
        (id, order_id, customer_id, region, status, priority, queue, escalated_by_oid,
         escalated_by_name, escalated_at, recommendation, summary, version,
         created_at, updated_at)
    VALUES
        (:id, :order_id, :customer_id, :region, :status, :priority, :queue, :escalated_by_oid,
         :escalated_by_name, :escalated_at, :recommendation, :summary, :version,
         :created_at, :updated_at)
    """
).bindparams(bindparam("recommendation", type_=JSONB(none_as_null=True)))

_INSERT_NOTES = text(
    """
    INSERT INTO core.ticket_notes
        (ticket_id, author_kind, author_oid, author_name, body, created_at)
    VALUES (:ticket_id, :author_kind, :author_oid, :author_name, :body, :created_at)
    """
)

_INSERT_PENDING_ACTIONS = text(
    """
    INSERT INTO control.pending_actions
        (id, tool, args, args_hash, requester_oid, requester_name, requester_region,
         approval_kind, policy_snapshot, idempotency_key, ticket_id, order_id, expires_at,
         created_at)
    VALUES
        (:id, :tool, :args, :args_hash, :requester_oid, :requester_name, :requester_region,
         :approval_kind, :policy_snapshot, :idempotency_key, :ticket_id,
         CAST(:args ->> 'order_id' AS bigint), :expires_at, :created_at)
    """
).bindparams(bindparam("args", type_=JSONB), bindparam("policy_snapshot", type_=JSONB))

_SET_NEXT_ID = text("SELECT setval(pg_get_serial_sequence(:table, 'id'), :next_id, false)")
_TABLES_WITH_SEEDED_IDS = (
    "core.customers",
    "core.orders",
    "core.tickets",
    "core.ticket_notes",
    "core.refunds",
)


async def load(connection: AsyncConnection, dataset: Dataset) -> None:
    inserts: tuple[tuple[TextClause, Sequence[Any]], ...] = (
        (_INSERT_CUSTOMERS, dataset.customers),
        (_INSERT_ORDERS, dataset.orders),
        (_INSERT_EVENTS, dataset.events),
        (_INSERT_TICKETS, dataset.tickets),
        (_INSERT_NOTES, dataset.notes),
        (_INSERT_PENDING_ACTIONS, dataset.pending_actions),
    )
    await connection.execute(_SET_REGION, {"region": ALL_REGIONS})
    await connection.execute(_EMPTY_EVERY_TABLE)
    for statement, rows in inserts:
        if rows:
            await connection.execute(statement, [asdict(row) for row in rows])
    for table in _TABLES_WITH_SEEDED_IDS:
        await connection.execute(_SET_NEXT_ID, {"table": table, "next_id": FIRST_ID_AFTER_SEED})

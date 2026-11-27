"""Puts the shared test world into the test database."""

from dataclasses import asdict
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncConnection

from opscp_mcp_testing.world import World

_ACTOR = UUID("90000000-0000-4000-8000-0000000000ff")

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
    "INSERT INTO core.tickets (id, order_id, customer_id, region, status, priority, queue,"
    " escalated_by_oid, escalated_by_name, escalated_at, recommendation, summary, version,"
    " updated_at) VALUES (:id, :order_id, :customer_id, :region, :status, :priority, :queue,"
    " :escalated_by_oid, :escalated_by_name, :escalated_at, :recommendation, :summary, :version,"
    " :updated_at)"
).bindparams(bindparam("recommendation", type_=JSONB(none_as_null=True)))
_INSERT_NOTE = text(
    "INSERT INTO core.ticket_notes (ticket_id, author_kind, author_name, body, created_at)"
    " VALUES (:ticket_id, :author_kind, :author_name, :body, :created_at)"
)
_INSERT_PENDING_ACTION = text(
    "INSERT INTO control.pending_actions (id, tool, args, args_hash, requester_oid,"
    " requester_name, requester_region, approval_kind, status, approver_oid, approver_name,"
    " decided_at, executed_at, idempotency_key, ticket_id, order_id, expires_at, created_at)"
    " VALUES (:id, :tool, :args, 'hash', :requester_oid, :requester_name, :region,"
    " :approval_kind, :status, :approver_oid, :approver_name, :decided_at, :executed_at,"
    " :idempotency_key, :ticket_id, :order_id, :expires_at, :created_at)"
).bindparams(bindparam("args", type_=JSONB))
_INSERT_REFUND = text(
    "INSERT INTO core.refunds (id, order_id, ticket_id, region, amount_minor, reason_code,"
    " reason_text, status, paid_at, failure_reason, requested_by_oid, requested_by_name,"
    " approved_by_oid, approved_by_name, pending_action_id, created_at) VALUES (:id, :order_id,"
    " :ticket_id, :region, :amount_minor, :reason_code, 'A test refund', :status, :paid_at,"
    " :failure_reason, :actor, :requested_by_name, :actor, :approved_by_name,"
    " :pending_action_id, :created_at)"
)


async def load_world(connection: AsyncConnection, world: World) -> None:
    await connection.execute(_INSERT_CUSTOMER, [asdict(row) for row in world.customers])
    await connection.execute(_INSERT_ORDER, [asdict(row) for row in world.orders])
    await connection.execute(_INSERT_EVENT, [asdict(row) for row in world.events])
    await connection.execute(_INSERT_TICKET, [asdict(row) for row in world.tickets])
    await connection.execute(_INSERT_NOTE, [asdict(row) for row in world.notes])
    order_of_ticket = {ticket.id: ticket.order_id for ticket in world.tickets}
    order_of_action = {
        action.id: order_of_ticket[action.ticket_id]
        for action in world.pending_actions
        if action.ticket_id is not None
    } | {refund.pending_action_id: refund.order_id for refund in world.refunds}
    await connection.execute(
        _INSERT_PENDING_ACTION,
        [
            {
                **asdict(row),
                "idempotency_key": str(row.id),
                "order_id": order_of_action.get(row.id),
                "decided_at": row.created_at if row.approver_oid else None,
                "executed_at": row.created_at if row.status == "executed" else None,
            }
            for row in world.pending_actions
        ],
    )
    await connection.execute(
        _INSERT_REFUND,
        [
            {
                **asdict(row),
                "actor": _ACTOR,
                "paid_at": row.created_at if row.status == "paid" else None,
            }
            for row in world.refunds
        ],
    )

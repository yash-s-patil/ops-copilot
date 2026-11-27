"""Pending actions, and the facts a refund is judged on, in PostgreSQL."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import ColumnElement, and_, exists, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql.expression import Executable

from opscp_common.vocabulary import (
    GOODWILL_DELAY,
    ISSUE_REFUND,
    REFUND_FAILED,
    REFUND_UNSETTLED,
    REQUEST_APPROVED,
    REQUEST_PROPOSED,
    SHIPPED,
)
from opscp_mcp.adapters.postgres.orders import last_new_scan_at
from opscp_mcp.adapters.postgres.session import Database
from opscp_mcp.adapters.postgres.tables import (
    customers,
    order_events,
    orders,
    pending_actions,
    refunds,
)
from opscp_mcp.ports.actions import ActionRecord, NewAction, RefundCase, WriteTool
from opscp_mcp.ports.meridian import Scope

# Requests on one order are stored one at a time: each takes this lock for its transaction.
_LOCK_NAMESPACE = "refund-request:"
_ONE_ORDER_AT_A_TIME = text("SELECT pg_advisory_xact_lock(hashtextextended(:order, 0))")
_ACTION = select(
    pending_actions.c.id,
    pending_actions.c.tool,
    pending_actions.c.args,
    pending_actions.c.args_hash,
    pending_actions.c.requester_oid,
    pending_actions.c.approval_kind,
    pending_actions.c.status,
    pending_actions.c.expires_at,
)
_REFUNDED = (
    select(func.coalesce(func.sum(refunds.c.amount_minor), 0))
    .where(refunds.c.order_id == orders.c.id, refunds.c.status != REFUND_FAILED)
    .scalar_subquery()
)


def _live_refund_request(now: datetime) -> ColumnElement[bool]:
    return and_(
        pending_actions.c.tool == ISSUE_REFUND,
        pending_actions.c.status.in_((REQUEST_PROPOSED, REQUEST_APPROVED)),
        pending_actions.c.expires_at > now,
    )


def _of_kind(kind: str) -> ColumnElement[bool]:
    return and_(order_events.c.order_id == orders.c.id, order_events.c.event_type == kind)


def _case(order_id: int, now: datetime) -> Executable:
    goodwill_given = exists().where(
        refunds.c.order_id == orders.c.id,
        refunds.c.reason_code == GOODWILL_DELAY,
        refunds.c.status != REFUND_FAILED,
    )
    request_waiting = exists().where(
        _live_refund_request(now), pending_actions.c.order_id == orders.c.id
    )
    return (
        select(
            orders.c.id.label("order_id"),
            orders.c.region,
            customers.c.tier,
            orders.c.status,
            orders.c.status_changed_at,
            orders.c.amount_minor,
            _REFUNDED.label("refunded_minor"),
            select(func.max(order_events.c.occurred_at))
            .where(_of_kind("carrier_exception"))
            .scalar_subquery()
            .label("carrier_exception_at"),
            exists().where(_of_kind("lost_in_transit_confirmed")).label("loss_confirmed"),
            exists().where(_of_kind("damage_reported")).label("damage_reported"),
            goodwill_given.label("goodwill_given"),
            request_waiting.label("request_waiting"),
        )
        .join(customers, customers.c.id == orders.c.customer_id)
        .where(orders.c.id == order_id)
    )


class PostgresRefundCases:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def for_order(self, scope: Scope, order_id: int, *, now: datetime) -> RefundCase | None:
        async def find(connection: AsyncConnection) -> RefundCase | None:
            row = (await connection.execute(_case(order_id, now))).mappings().one_or_none()
            if row is None:
                return None
            shipped = row["status"] == SHIPPED
            scan = await last_new_scan_at(connection, order_id) if shipped else None
            return RefundCase(**row, last_new_scan_at=scan)

        return await self._database.read(scope, find)

    async def unfinished_on_ticket(self, scope: Scope, ticket_id: int, *, now: datetime) -> bool:
        unsettled = exists().where(
            refunds.c.ticket_id == ticket_id, refunds.c.status.in_(REFUND_UNSETTLED)
        )
        waiting = exists().where(
            _live_refund_request(now), pending_actions.c.ticket_id == ticket_id
        )

        async def find(connection: AsyncConnection) -> bool:
            return bool((await connection.execute(select(or_(unsettled, waiting)))).scalar_one())

        return await self._database.read(scope, find)


class PostgresPendingActions:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def find(
        self, scope: Scope, tool: WriteTool, idempotency_key: str
    ) -> ActionRecord | None:
        async def find(connection: AsyncConnection) -> ActionRecord | None:
            row = (
                (await connection.execute(_mine(scope.user_oid, tool, idempotency_key)))
                .mappings()
                .one_or_none()
            )
            return None if row is None else ActionRecord.model_validate(dict(row))

        return await self._database.read(scope, find)

    async def propose(self, new: NewAction, *, now: datetime) -> ActionRecord | None:
        added = (
            insert(pending_actions)
            .values(id=uuid4(), **new.model_dump())
            .on_conflict_do_nothing(constraint="pending_actions_idempotency_key")
        )
        mine = _mine(new.requester_oid, new.tool, new.idempotency_key)
        waiting = select(
            exists().where(_live_refund_request(now), pending_actions.c.order_id == new.order_id)
        )

        async def store(connection: AsyncConnection) -> ActionRecord | None:
            if new.order_id is not None:
                held = {"order": f"{_LOCK_NAMESPACE}{new.order_id}"}
                await connection.execute(_ONE_ORDER_AT_A_TIME, held)
                earlier = (await connection.execute(mine)).mappings().one_or_none()
                if earlier is not None:
                    return ActionRecord.model_validate(dict(earlier))
                if (await connection.execute(waiting)).scalar_one():
                    return None
            await connection.execute(added)
            row = (await connection.execute(mine)).mappings().one()
            return ActionRecord.model_validate(dict(row))

        return await self._database.write(store)


def _mine(requester_oid: object, tool: str, idempotency_key: str) -> Executable:
    return _ACTION.where(
        and_(
            pending_actions.c.requester_oid == requester_oid,
            pending_actions.c.tool == tool,
            pending_actions.c.idempotency_key == idempotency_key,
        )
    )

"""Pending actions, and the facts a refund is judged on, in PostgreSQL."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import ColumnElement, and_, exists, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql.expression import Executable

from opscp_common.vocabulary import (
    ALL_REGIONS,
    GOODWILL_DELAY,
    ISSUE_REFUND,
    REFUND_UNSETTLED,
    REQUEST_APPROVED,
    REQUEST_PROPOSED,
    SHIPPED,
    SUPERVISOR_APPROVAL,
)
from opscp_mcp.adapters.postgres.audit import append
from opscp_mcp.adapters.postgres.orders import last_new_scan_at
from opscp_mcp.adapters.postgres.session import Database
from opscp_mcp.adapters.postgres.tables import (
    REFUND_COUNTS,
    customers,
    order_events,
    orders,
    pending_actions,
    refunds,
)
from opscp_mcp.ports.actions import (
    ActionRecord,
    ActionsWanted,
    Decided,
    Decision,
    NewAction,
    RefundCase,
    WriteTool,
)
from opscp_mcp.ports.controls import AuditEntry
from opscp_mcp.ports.meridian import Scope

# Requests on one order are stored one at a time: each takes this lock for its transaction.
_LOCK_NAMESPACE = "refund-request:"
LOCK_ONE_ORDER = text("SELECT pg_advisory_xact_lock(hashtextextended(:order, 0))")
_ACTION = select(
    pending_actions.c.id,
    pending_actions.c.tool,
    pending_actions.c.args,
    pending_actions.c.args_hash,
    pending_actions.c.requester_oid,
    pending_actions.c.requester_name,
    pending_actions.c.requester_region,
    pending_actions.c.approval_kind,
    pending_actions.c.status,
    pending_actions.c.approver_oid,
    pending_actions.c.approver_name,
    pending_actions.c.decided_at,
    pending_actions.c.decision_reason,
    pending_actions.c.policy_snapshot,
    pending_actions.c.ticket_id,
    pending_actions.c.result,
    pending_actions.c.expires_at,
    pending_actions.c.created_at,
)


def _counted_refunds(leaving_out: int | None) -> ColumnElement[bool]:
    """The order's refunds that count against it, without the one being judged, if any."""
    counted = and_(refunds.c.order_id == orders.c.id, REFUND_COUNTS)
    return counted if leaving_out is None else and_(counted, refunds.c.id != leaving_out)


def _live_refund_request(now: datetime) -> ColumnElement[bool]:
    return and_(
        pending_actions.c.tool == ISSUE_REFUND,
        pending_actions.c.status.in_((REQUEST_PROPOSED, REQUEST_APPROVED)),
        pending_actions.c.expires_at > now,
    )


def _of_kind(kind: str) -> ColumnElement[bool]:
    return and_(order_events.c.order_id == orders.c.id, order_events.c.event_type == kind)


def _case(order_id: int, now: datetime, leaving_out: int | None) -> Executable:
    counted = _counted_refunds(leaving_out)
    refunded = (
        select(func.coalesce(func.sum(refunds.c.amount_minor), 0)).where(counted).scalar_subquery()
    )
    goodwill_given = exists().where(counted, refunds.c.reason_code == GOODWILL_DELAY)
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
            refunded.label("refunded_minor"),
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


def lock_key(order_id: int) -> str:
    """What a transaction locks to be the only one writing refunds or requests on an order."""
    return f"{_LOCK_NAMESPACE}{order_id}"


async def refund_case(
    connection: AsyncConnection, order_id: int, now: datetime, *, leaving_out: int | None = None
) -> RefundCase | None:
    """The facts of the order as the connection's scope sees them, or None.

    `leaving_out` names a refund to judge the order without: the one being sent round again.
    """
    statement = _case(order_id, now, leaving_out)
    row = (await connection.execute(statement)).mappings().one_or_none()
    if row is None:
        return None
    shipped = row["status"] == SHIPPED
    scan = await last_new_scan_at(connection, order_id) if shipped else None
    return RefundCase(**row, last_new_scan_at=scan)


async def refund_unfinished_on(connection: AsyncConnection, ticket_id: int, now: datetime) -> bool:
    """Whether a refund on the ticket is on its way to being paid, or waits for a decision."""
    unsettled = exists().where(
        refunds.c.ticket_id == ticket_id, refunds.c.status.in_(REFUND_UNSETTLED)
    )
    waiting = exists().where(_live_refund_request(now), pending_actions.c.ticket_id == ticket_id)
    return bool((await connection.execute(select(or_(unsettled, waiting)))).scalar_one())


class PostgresRefundCases:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def for_order(self, scope: Scope, order_id: int, *, now: datetime) -> RefundCase | None:
        return await self._database.read(
            scope, lambda connection: refund_case(connection, order_id, now)
        )

    async def unfinished_on_ticket(self, scope: Scope, ticket_id: int, *, now: datetime) -> bool:
        return await self._database.read(
            scope, lambda connection: refund_unfinished_on(connection, ticket_id, now)
        )


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
                await connection.execute(LOCK_ONE_ORDER, {"order": lock_key(new.order_id)})
                earlier = (await connection.execute(mine)).mappings().one_or_none()
                if earlier is not None:
                    return ActionRecord.model_validate(dict(earlier))
                if (await connection.execute(waiting)).scalar_one():
                    return None
            await connection.execute(added)
            row = (await connection.execute(mine)).mappings().one()
            return ActionRecord.model_validate(dict(row))

        return await self._database.write(store)

    async def get(self, action_id: UUID) -> ActionRecord | None:
        async def find(connection: AsyncConnection) -> ActionRecord | None:
            found = await connection.execute(_ACTION.where(pending_actions.c.id == action_id))
            row = found.mappings().one_or_none()
            return None if row is None else ActionRecord.model_validate(dict(row))

        return await self._database.lookup(find)

    async def decide(
        self, action_id: UUID, decision: Decision, entry: AuditEntry
    ) -> Decided | None:
        decided = (
            update(pending_actions)
            .where(
                pending_actions.c.id == action_id,
                pending_actions.c.status == REQUEST_PROPOSED,
                pending_actions.c.expires_at > decision.decided_at,
                pending_actions.c.args_hash == decision.seen_hash,
            )
            .values(
                status=decision.status,
                approver_oid=decision.approver_oid,
                approver_name=decision.approver_name,
                decision_reason=decision.reason,
                decided_at=decision.decided_at,
                expires_at=decision.expires_at,
            )
            .returning(*_ACTION.selected_columns)
        )

        async def store(connection: AsyncConnection) -> Decided | None:
            row = (await connection.execute(decided)).mappings().one_or_none()
            if row is None:
                return None
            return Decided(
                action=ActionRecord.model_validate(dict(row)),
                audit_id=await append(connection, entry),
            )

        return await self._database.write(store)

    async def waiting(self, wanted: ActionsWanted, *, now: datetime) -> tuple[ActionRecord, ...]:
        mine = pending_actions.c.requester_oid == wanted.requester_oid
        visible = mine
        if wanted.awaiting_supervisor_in is not None:
            theirs = and_(
                pending_actions.c.approval_kind == SUPERVISOR_APPROVAL,
                pending_actions.c.status == REQUEST_PROPOSED,
            )
            if wanted.awaiting_supervisor_in != ALL_REGIONS:
                theirs = and_(
                    theirs, pending_actions.c.requester_region == wanted.awaiting_supervisor_in
                )
            visible = or_(mine, theirs)
        listed = (
            _ACTION.where(
                visible,
                pending_actions.c.status.in_((REQUEST_PROPOSED, REQUEST_APPROVED)),
                pending_actions.c.expires_at > now,
            )
            .order_by(pending_actions.c.created_at.desc(), pending_actions.c.id)
            .limit(wanted.limit)
        )
        if wanted.ticket_id is not None:
            listed = listed.where(pending_actions.c.ticket_id == wanted.ticket_id)

        async def find(connection: AsyncConnection) -> tuple[ActionRecord, ...]:
            rows = (await connection.execute(listed)).mappings().all()
            return tuple(ActionRecord.model_validate(dict(row)) for row in rows)

        return await self._database.lookup(find)


def _mine(requester_oid: object, tool: str, idempotency_key: str) -> Executable:
    return _ACTION.where(
        and_(
            pending_actions.c.requester_oid == requester_oid,
            pending_actions.c.tool == tool,
            pending_actions.c.idempotency_key == idempotency_key,
        )
    )

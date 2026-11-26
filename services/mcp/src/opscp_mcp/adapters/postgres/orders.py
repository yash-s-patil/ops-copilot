from datetime import datetime

from pydantic import JsonValue
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from opscp_common.vocabulary import CARRIER_SCAN, REFUND_FAILED, SHIPPED, EventType
from opscp_mcp.adapters.postgres.session import Database
from opscp_mcp.adapters.postgres.tables import order_events, orders, refunds
from opscp_mcp.ports.meridian import EventRecord, OrderRecord, Scope

_REFUNDED = (
    select(func.coalesce(func.sum(refunds.c.amount_minor), 0))
    .where(refunds.c.order_id == orders.c.id, refunds.c.status != REFUND_FAILED)
    .scalar_subquery()
)
_ORDER = select(
    orders.c.id,
    orders.c.customer_id,
    orders.c.status,
    orders.c.amount_minor,
    orders.c.currency,
    orders.c.region,
    orders.c.carrier,
    orders.c.tracking_ref,
    orders.c.placed_at,
    orders.c.status_changed_at,
    _REFUNDED.label("refunded_minor"),
)
_NEWEST_FIRST = (order_events.c.occurred_at.desc(), order_events.c.id.desc())


class PostgresOrders:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(self, scope: Scope, order_id: int, *, recent_events: int) -> OrderRecord | None:
        async def find(connection: AsyncConnection) -> OrderRecord | None:
            row = (
                (await connection.execute(_ORDER.where(orders.c.id == order_id)))
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            shipped = row["status"] == SHIPPED
            return OrderRecord(
                **row,
                last_new_scan_at=await _last_new_scan_at(connection, order_id) if shipped else None,
                recent_events=await _recent_events(connection, order_id, recent_events),
                event_counts=await _event_counts(connection, order_id) if recent_events else {},
            )

        return await self._database.read(scope, find)


async def _recent_events(
    connection: AsyncConnection, order_id: int, limit: int
) -> tuple[EventRecord, ...]:
    if not limit:
        return ()
    rows = await connection.execute(
        select(
            order_events.c.event_type.label("type"),
            order_events.c.occurred_at,
            order_events.c.payload,
        )
        .where(order_events.c.order_id == order_id)
        .order_by(*_NEWEST_FIRST)
        .limit(limit)
    )
    return tuple(
        EventRecord(type=row.type, occurred_at=row.occurred_at, payload=details(row.payload))
        for row in rows
    )


def details(payload: JsonValue) -> dict[str, JsonValue]:
    """The column accepts any JSON; only an object carries details."""
    return payload if isinstance(payload, dict) else {}


async def _event_counts(connection: AsyncConnection, order_id: int) -> dict[EventType, int]:
    rows = await connection.execute(
        select(order_events.c.event_type, func.count().label("total"))
        .where(order_events.c.order_id == order_id)
        .group_by(order_events.c.event_type)
    )
    return {row.event_type: row.total for row in rows}


async def _last_new_scan_at(connection: AsyncConnection, order_id: int) -> datetime | None:
    scans = (
        select(
            order_events.c.occurred_at,
            order_events.c.payload,
            func.lag(order_events.c.payload)
            .over(order_by=(order_events.c.occurred_at, order_events.c.id))
            .label("previous_payload"),
        )
        .where(order_events.c.order_id == order_id, order_events.c.event_type == CARRIER_SCAN)
        .subquery()
    )
    return await connection.scalar(
        select(func.max(scans.c.occurred_at)).where(
            scans.c.payload.is_distinct_from(scans.c.previous_payload)
        )
    )

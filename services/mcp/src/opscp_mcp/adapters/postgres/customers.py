from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from opscp_common.vocabulary import OPEN_TICKET_STATUSES
from opscp_mcp.adapters.postgres.session import Database
from opscp_mcp.adapters.postgres.tables import customers, tickets
from opscp_mcp.ports.meridian import CustomerRecord, Scope

_OPEN_TICKETS = (
    select(func.count())
    .where(tickets.c.customer_id == customers.c.id, tickets.c.status.in_(OPEN_TICKET_STATUSES))
    .scalar_subquery()
)
_CUSTOMER = select(
    customers.c.id,
    customers.c.full_name,
    customers.c.email,
    customers.c.phone,
    customers.c.tier,
    customers.c.region,
    customers.c.created_at,
    _OPEN_TICKETS.label("open_ticket_count"),
)


class PostgresCustomers:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def by_id(self, scope: Scope, customer_id: int) -> CustomerRecord | None:
        return await self._one(scope, customers.c.id == customer_id)

    async def by_email(self, scope: Scope, email: str) -> CustomerRecord | None:
        return await self._one(scope, func.lower(customers.c.email) == func.lower(email))

    async def _one(self, scope: Scope, condition: ColumnElement[bool]) -> CustomerRecord | None:
        async def find(connection: AsyncConnection) -> CustomerRecord | None:
            row = (await connection.execute(_CUSTOMER.where(condition))).mappings().one_or_none()
            return None if row is None else CustomerRecord.model_validate(dict(row))

        return await self._database.read(scope, find)

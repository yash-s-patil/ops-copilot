import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from opscp_mcp.adapters.postgres.session import read_as
from opscp_mcp.ports.meridian import Scope
from opscp_mcp_testing.cases.customers import CustomerCases
from opscp_mcp_testing.cases.orders import OrderCases
from opscp_mcp_testing.identities import PRIYA

pytestmark = pytest.mark.integration

WEST = Scope(user_oid=PRIYA.oid, region="west")


class TestCustomersInPostgres(CustomerCases):
    pass


class TestOrdersInPostgres(OrderCases):
    pass


async def test_a_read_cannot_write(mcp_app: AsyncEngine) -> None:
    async with read_as(mcp_app, WEST) as connection:
        with pytest.raises(DBAPIError, match="read-only transaction"):
            await connection.execute(
                text("UPDATE core.tickets SET summary = summary WHERE id = :id"), {"id": 201}
            )


async def test_the_scope_does_not_outlive_its_transaction(mcp_app: AsyncEngine) -> None:
    async with read_as(mcp_app, WEST) as connection:
        inside = await connection.scalar(text("SELECT current_setting('app.region', true)"))
    async with mcp_app.connect() as connection:
        after = await connection.scalar(text("SELECT current_setting('app.region', true)"))

    assert inside == "west"
    assert not after


async def test_the_scope_is_dropped_when_the_read_fails(mcp_app: AsyncEngine) -> None:
    with pytest.raises(DBAPIError):
        async with read_as(mcp_app, WEST) as connection:
            await connection.execute(text("SELECT 1 / 0"))
    async with mcp_app.connect() as connection:
        region = await connection.scalar(text("SELECT current_setting('app.region', true)"))
        user = await connection.scalar(text("SELECT current_setting('app.user_oid', true)"))

    assert not region
    assert not user


async def test_a_read_sees_one_snapshot(mcp_app: AsyncEngine) -> None:
    async with read_as(mcp_app, WEST) as connection:
        level = await connection.scalar(text("SHOW transaction_isolation"))

    assert level == "repeatable read"

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from .database import SOUTH, WEST, transaction

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("fixture_rows")]

SOUTH_CUSTOMER = 2


async def test_an_order_cannot_point_at_a_customer_in_another_region(migrator: AsyncEngine) -> None:
    async with transaction(migrator, region="*") as connection:
        with pytest.raises(IntegrityError, match="orders_customer_region_fkey"):
            await connection.execute(
                text(
                    "INSERT INTO core.orders (customer_id, status, amount_minor, region,"
                    " placed_at, status_changed_at)"
                    " VALUES (:customer, 'paid', 1000, 'west', now(), now())"
                ),
                {"customer": SOUTH_CUSTOMER},
            )


async def test_a_ticket_cannot_point_at_a_customer_in_another_region(
    migrator: AsyncEngine,
) -> None:
    async with transaction(migrator, region="*") as connection:
        with pytest.raises(IntegrityError, match="tickets_customer_region_fkey"):
            await connection.execute(
                text(
                    "INSERT INTO core.tickets (order_id, customer_id, region, priority, summary)"
                    " VALUES (:order, :customer, 'west', 'P3', 'x')"
                ),
                {"order": WEST["order"], "customer": SOUTH_CUSTOMER},
            )


async def test_rows_in_their_own_region_are_still_accepted(migrator: AsyncEngine) -> None:
    async with transaction(migrator, region="*") as connection:
        await connection.execute(
            text(
                "INSERT INTO core.tickets (order_id, customer_id, region, priority, summary)"
                " VALUES (:order, :customer, 'south', 'P3', 'x')"
            ),
            {"order": SOUTH["order"], "customer": SOUTH_CUSTOMER},
        )

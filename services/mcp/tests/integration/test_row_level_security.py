import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from .database import SOUTH, SOUTH_ENGINEER, WEST, WEST_ENGINEER, transaction

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("fixture_rows")]

_COUNT = {
    "customers": text("SELECT count(*) FROM core.customers"),
    "orders": text("SELECT count(*) FROM core.orders"),
    "order_events": text("SELECT count(*) FROM core.order_events"),
    "tickets": text("SELECT count(*) FROM core.tickets"),
    "ticket_notes": text("SELECT count(*) FROM core.ticket_notes"),
    "refunds": text("SELECT count(*) FROM core.refunds"),
}
RLS_TABLES = tuple(_COUNT)
_CHILD_ROWS_OF = {
    "order_events": text("SELECT count(*) FROM core.order_events WHERE order_id = :parent"),
    "ticket_notes": text("SELECT count(*) FROM core.ticket_notes WHERE ticket_id = :parent"),
}
_NEW_PENDING_ACTION = text(
    """
    INSERT INTO control.pending_actions
        (tool, args, args_hash, requester_oid, requester_name, requester_region,
         approval_kind, idempotency_key, expires_at)
    VALUES ('issue_refund', '{}'::jsonb, 'hash', :oid, 'West Engineer', 'west', 'self',
            :key, now() + interval '15 minutes')
    RETURNING id
    """
)
_NEW_REFUND = text(
    """
    INSERT INTO core.refunds
        (order_id, region, amount_minor, reason_code, reason_text, requested_by_oid,
         requested_by_name, approved_by_oid, approved_by_name, pending_action_id)
    VALUES (:order_id, :region, 1000, 'goodwill_delay', 'x', :oid, 'West Engineer', :oid,
            'West Engineer', :pending_action_id)
    """
)
_ORDER_IDS = text("SELECT id FROM core.orders ORDER BY id")


async def _order_ids(connection: AsyncConnection) -> list[int]:
    return list((await connection.execute(_ORDER_IDS)).scalars())


async def test_west_engineer_cannot_read_a_south_order(mcp_app: AsyncEngine) -> None:
    async with transaction(mcp_app, region="west") as connection:
        assert await _order_ids(connection) == [WEST["order"]]


async def test_south_engineer_cannot_read_a_west_order(mcp_app: AsyncEngine) -> None:
    async with transaction(mcp_app, region="south", user_oid=SOUTH_ENGINEER) as connection:
        assert await _order_ids(connection) == [SOUTH["order"]]


async def test_supervisor_region_sees_every_region(mcp_app: AsyncEngine) -> None:
    async with transaction(mcp_app, region="*") as connection:
        assert await _order_ids(connection) == [WEST["order"], SOUTH["order"]]


@pytest.mark.parametrize("table", RLS_TABLES)
async def test_no_region_returns_no_rows(mcp_app: AsyncEngine, table: str) -> None:
    async with transaction(mcp_app) as connection:
        assert (await connection.execute(_COUNT[table])).scalar_one() == 0


@pytest.mark.parametrize("table", RLS_TABLES)
async def test_region_does_not_leak_into_the_next_transaction_on_a_connection(
    mcp_app: AsyncEngine, table: str
) -> None:
    async with mcp_app.connect() as connection:
        async with connection.begin():
            await connection.execute(
                text("SELECT set_config('app.region', 'west', true)"),
            )
            assert (await connection.execute(_COUNT[table])).scalar_one() > 0
        async with connection.begin():
            assert (await connection.execute(_COUNT[table])).scalar_one() == 0


@pytest.mark.parametrize("region", ["", "WEST", "west ", "north", "%", "west' OR '1'='1"])
async def test_unknown_region_value_returns_no_rows(mcp_app: AsyncEngine, region: str) -> None:
    async with transaction(mcp_app, region=region) as connection:
        assert await _order_ids(connection) == []


@pytest.mark.parametrize(
    ("table", "south_parent"),
    [("order_events", SOUTH["order"]), ("ticket_notes", SOUTH["ticket"])],
)
async def test_child_rows_of_another_region_are_hidden(
    mcp_app: AsyncEngine, table: str, south_parent: int
) -> None:
    async with transaction(mcp_app, region="west") as connection:
        result = await connection.execute(_CHILD_ROWS_OF[table], {"parent": south_parent})
        assert result.scalar_one() == 0


async def test_update_of_another_regions_ticket_changes_nothing(mcp_app: AsyncEngine) -> None:
    async with transaction(mcp_app, region="west") as connection:
        result = await connection.execute(
            text("UPDATE core.tickets SET status = 'resolved' WHERE id = :id"),
            {"id": SOUTH["ticket"]},
        )
        assert result.rowcount == 0


async def test_note_cannot_be_added_to_another_regions_ticket(mcp_app: AsyncEngine) -> None:
    async with transaction(mcp_app, region="west") as connection:
        with pytest.raises(DBAPIError, match="row-level security"):
            await connection.execute(
                text(
                    "INSERT INTO core.ticket_notes (ticket_id, author_kind, author_name, body) "
                    "VALUES (:ticket, 'engineer', 'West Engineer', 'note')"
                ),
                {"ticket": SOUTH["ticket"]},
            )


@pytest.mark.parametrize(
    ("order_id", "region", "error"),
    [
        (SOUTH["order"], "west", "refunds_order_region_fkey"),
        (SOUTH["order"], "south", "row-level security"),
        (WEST["order"], "south", "row-level security"),
    ],
    ids=["south order labelled west", "south order", "west order labelled south"],
)
async def test_refund_cannot_be_created_across_regions(
    mcp_app: AsyncEngine, order_id: int, region: str, error: str
) -> None:
    async with transaction(mcp_app, region="west") as connection:
        pending_action_id = (
            await connection.execute(
                _NEW_PENDING_ACTION, {"oid": WEST_ENGINEER, "key": f"{order_id}-{region}"}
            )
        ).scalar_one()

        with pytest.raises(DBAPIError, match=error):
            await connection.execute(
                _NEW_REFUND,
                {
                    "order_id": order_id,
                    "region": region,
                    "oid": WEST_ENGINEER,
                    "pending_action_id": pending_action_id,
                },
            )


async def test_refund_can_be_created_in_the_callers_own_region(mcp_app: AsyncEngine) -> None:
    async with transaction(mcp_app, region="west") as connection:
        pending_action_id = (
            await connection.execute(_NEW_PENDING_ACTION, {"oid": WEST_ENGINEER, "key": "own"})
        ).scalar_one()
        result = await connection.execute(
            _NEW_REFUND,
            {
                "order_id": WEST["order"],
                "region": "west",
                "oid": WEST_ENGINEER,
                "pending_action_id": pending_action_id,
            },
        )
        assert result.rowcount == 1

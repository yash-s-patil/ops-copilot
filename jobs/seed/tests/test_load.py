from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from opscp_common.settings import DatabaseSettings
from opscp_seed.load import FIRST_ID_AFTER_SEED, load
from opscp_seed.model import Dataset

from .conftest import ARJUN, REPO_ROOT

pytestmark = pytest.mark.integration

TEST_DATABASE = "meridian_test"

COUNTS = {
    "core.customers": "SELECT count(*) FROM core.customers",
    "core.orders": "SELECT count(*) FROM core.orders",
    "core.order_events": "SELECT count(*) FROM core.order_events",
    "core.tickets": "SELECT count(*) FROM core.tickets",
    "core.ticket_notes": "SELECT count(*) FROM core.ticket_notes",
    "control.pending_actions": "SELECT count(*) FROM control.pending_actions",
}


def _expected_counts(dataset: Dataset) -> dict[str, int]:
    return {
        "core.customers": len(dataset.customers),
        "core.orders": len(dataset.orders),
        "core.order_events": len(dataset.events),
        "core.tickets": len(dataset.tickets),
        "core.ticket_notes": len(dataset.notes),
        "control.pending_actions": len(dataset.pending_actions),
    }


async def _counts(connection: AsyncConnection) -> dict[str, int]:
    return {
        table: (await connection.execute(text(query))).scalar_one()
        for table, query in COUNTS.items()
    }


@pytest.fixture(scope="module")
def migrated_database() -> DatabaseSettings:
    base = DatabaseSettings(_env_file=REPO_ROOT / ".env")  # pyright: ignore[reportCallIssue] -- fields come from the env file
    settings = base.model_copy(update={"user": "migrator", "name": TEST_DATABASE})
    config = Config(REPO_ROOT / "services" / "mcp" / "alembic.ini")
    config.attributes["database_settings"] = settings
    command.upgrade(config, "head")
    return settings


@pytest_asyncio.fixture(scope="module")
async def seeded(
    migrated_database: DatabaseSettings, dataset: Dataset
) -> AsyncGenerator[AsyncConnection]:
    """The dataset loaded as `migrator` in a transaction that is rolled back afterwards."""
    engine = create_async_engine(migrated_database.url())
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await load(connection, dataset)
            yield connection
        finally:
            await transaction.rollback()
    await engine.dispose()


async def test_every_row_of_the_dataset_is_loaded(
    seeded: AsyncConnection, dataset: Dataset
) -> None:
    assert await _counts(seeded) == _expected_counts(dataset)


async def test_loading_again_replaces_the_data_instead_of_adding_to_it(
    seeded: AsyncConnection, dataset: Dataset
) -> None:
    await load(seeded, dataset)

    assert await _counts(seeded) == _expected_counts(dataset)


async def test_an_empty_dataset_leaves_every_table_empty(seeded: AsyncConnection) -> None:
    empty = Dataset(customers=(), orders=(), events=(), tickets=(), notes=(), pending_actions=())
    async with seeded.begin_nested() as savepoint:
        await load(seeded, empty)
        counts = await _counts(seeded)
        await savepoint.rollback()

    assert set(counts.values()) == {0}


async def test_json_columns_hold_objects_and_missing_recommendations_are_null(
    seeded: AsyncConnection,
) -> None:
    row = (
        await seeded.execute(
            text(
                """
                SELECT
                    (SELECT payload ->> 'reason' FROM core.order_events
                      WHERE order_id = 4471 ORDER BY occurred_at DESC LIMIT 1),
                    (SELECT count(*) FROM core.tickets
                      WHERE queue = 'L1' AND recommendation IS NOT NULL),
                    (SELECT count(*) FROM core.tickets
                      WHERE jsonb_typeof(recommendation) = 'object')
                """
            )
        )
    ).one()

    assert row[0] == "postcode_mismatch"
    assert row[1] == 0
    assert row[2] > 0


async def test_the_supervisor_request_is_proposed_by_arjun_and_expires_in_24_hours(
    seeded: AsyncConnection,
) -> None:
    row = (
        await seeded.execute(
            text(
                """
                SELECT status, requester_oid, args ->> 'order_id',
                       expires_at - created_at = interval '24 hours'
                FROM control.pending_actions
                """
            )
        )
    ).one()

    assert tuple(row) == ("proposed", ARJUN.oid, "3912", True)


@pytest.mark.parametrize(
    "insert",
    [
        """
        INSERT INTO core.customers (full_name, email, tier, region)
        VALUES ('New Customer', 'new.customer@example.in', 'standard', 'west') RETURNING id
        """,
        """
        INSERT INTO core.orders
            (customer_id, status, amount_minor, region, placed_at, status_changed_at)
        VALUES (1001, 'placed', 100, 'west', now(), now()) RETURNING id
        """,
        """
        INSERT INTO core.tickets (order_id, customer_id, region, priority, summary)
        VALUES (4471, 1001, 'west', 'P4', 'New ticket') RETURNING id
        """,
        """
        INSERT INTO core.ticket_notes (ticket_id, author_kind, author_name, body)
        VALUES (2207, 'system', 'Payments', 'New note') RETURNING id
        """,
    ],
)
async def test_new_rows_get_ids_from_5001(seeded: AsyncConnection, insert: str) -> None:
    async with seeded.begin_nested() as savepoint:
        new_id = (await seeded.execute(text(insert))).scalar_one()
        await savepoint.rollback()

    assert new_id == FIRST_ID_AFTER_SEED

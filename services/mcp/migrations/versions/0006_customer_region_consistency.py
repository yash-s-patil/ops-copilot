"""An order or ticket cannot carry one region and point at another region's customer."""

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | None = None
depends_on: str | None = None

UPGRADE: tuple[str, ...] = (
    "ALTER TABLE core.customers ADD CONSTRAINT customers_id_region_key UNIQUE (id, region)",
    """
    ALTER TABLE core.orders
        ADD CONSTRAINT orders_customer_region_fkey
        FOREIGN KEY (customer_id, region) REFERENCES core.customers (id, region)
    """,
    """
    ALTER TABLE core.tickets
        ADD CONSTRAINT tickets_customer_region_fkey
        FOREIGN KEY (customer_id, region) REFERENCES core.customers (id, region)
    """,
)

DOWNGRADE: tuple[str, ...] = (
    "ALTER TABLE core.tickets DROP CONSTRAINT tickets_customer_region_fkey",
    "ALTER TABLE core.orders DROP CONSTRAINT orders_customer_region_fkey",
    "ALTER TABLE core.customers DROP CONSTRAINT customers_id_region_key",
)


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)

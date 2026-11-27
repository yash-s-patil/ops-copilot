"""A refund request names its order in a column, so a waiting one can be found by index.

Rows written before the column existed take it from their arguments where they can. The rule
that a refund request names its order is not checked against those rows, only new ones.
"""

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | None = None
depends_on: str | None = None

UPGRADE: tuple[str, ...] = (
    "ALTER TABLE control.pending_actions ADD COLUMN order_id bigint",
    """
    UPDATE control.pending_actions
    SET order_id = CAST(args ->> 'order_id' AS bigint)
    WHERE tool = 'issue_refund' AND args ->> 'order_id' ~ '^[0-9]{1,18}$'
    """,
    """
    ALTER TABLE control.pending_actions
        ADD CONSTRAINT pending_actions_refund_order_check
        CHECK (tool <> 'issue_refund' OR order_id IS NOT NULL) NOT VALID
    """,
    """
    CREATE INDEX pending_actions_live_refund_order_idx
        ON control.pending_actions (order_id)
        WHERE tool = 'issue_refund' AND status IN ('proposed', 'approved')
    """,
)

DOWNGRADE: tuple[str, ...] = (
    "DROP INDEX control.pending_actions_live_refund_order_idx",
    "ALTER TABLE control.pending_actions DROP CONSTRAINT pending_actions_refund_order_check",
    "ALTER TABLE control.pending_actions DROP COLUMN order_id",
)


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)

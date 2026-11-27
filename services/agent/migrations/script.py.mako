"""${message}"""

from alembic import op

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | None = None
depends_on: str | None = None

UPGRADE: tuple[str, ...] = ()
DOWNGRADE: tuple[str, ...] = ()


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)

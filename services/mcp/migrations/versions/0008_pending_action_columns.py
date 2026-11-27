"""The MCP server may change only the columns of a pending action that record its progress.

What was asked for (the tool, the arguments, their hash, the requester, the kind of approval)
is fixed when the row is written. An approval is checked against those columns, so the role
that approves must not be able to change them.
"""

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | None = None
depends_on: str | None = None

_PROGRESS = (
    "status, approver_oid, approver_name, decided_at, decision_reason, expires_at, "
    "executed_at, result"
)


def upgrade() -> None:
    op.execute("REVOKE UPDATE ON control.pending_actions FROM mcp_app")
    op.execute(f"GRANT UPDATE ({_PROGRESS}) ON control.pending_actions TO mcp_app")


def downgrade() -> None:
    op.execute(f"REVOKE UPDATE ({_PROGRESS}) ON control.pending_actions FROM mcp_app")
    op.execute("GRANT UPDATE ON control.pending_actions TO mcp_app")

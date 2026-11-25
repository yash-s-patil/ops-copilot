"""Grants for the application roles. The roles themselves are created by the bootstrap."""

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | None = None
depends_on: str | None = None

UPGRADE: tuple[str, ...] = (
    "GRANT USAGE ON SCHEMA core, control, audit TO mcp_app, payments_worker",
    "GRANT SELECT ON ALL TABLES IN SCHEMA core TO mcp_app",
    """
    GRANT UPDATE (
        status, queue, escalated_by_oid, escalated_by_name, escalated_at, recommendation,
        version, updated_at
    ) ON core.tickets TO mcp_app
    """,
    "GRANT INSERT ON core.ticket_notes TO mcp_app",
    "GRANT INSERT ON core.refunds TO mcp_app",
    "GRANT UPDATE (status, updated_at) ON core.refunds TO mcp_app",
    "GRANT SELECT ON ALL TABLES IN SCHEMA control TO mcp_app",
    "GRANT INSERT, UPDATE ON control.pending_actions TO mcp_app",
    "GRANT INSERT ON control.approval_token_uses TO mcp_app",
    "GRANT INSERT ON control.outbox TO mcp_app",
    "GRANT INSERT, UPDATE ON control.rate_limits TO mcp_app",
    "GRANT INSERT, UPDATE ON control.chaos_flags TO mcp_app",
    "GRANT SELECT ON audit.audit_log TO mcp_app",
    "GRANT SELECT, UPDATE ON control.outbox TO payments_worker",
    "GRANT SELECT ON core.refunds TO payments_worker",
    """
    GRANT UPDATE (status, provider_ref, paid_at, failure_reason, updated_at)
        ON core.refunds TO payments_worker
    """,
    "GRANT INSERT ON core.ticket_notes TO payments_worker",
    "GRANT SELECT ON control.chaos_flags TO payments_worker",
    "GRANT UPDATE (enabled) ON control.chaos_flags TO payments_worker",
    """
    GRANT EXECUTE ON FUNCTION audit.append(
        text, text, uuid, text[], text, text, text, jsonb, text, text, text, integer, integer
    ) TO mcp_app, payments_worker
    """,
    """
    GRANT EXECUTE ON FUNCTION audit.compute_row_hash(
        text, text, timestamptz, text, text, uuid, text[], text, text, text, jsonb, text, text,
        text, integer, integer
    ) TO mcp_app
    """,
)

DOWNGRADE: tuple[str, ...] = (
    """
    REVOKE ALL ON FUNCTION audit.compute_row_hash(
        text, text, timestamptz, text, text, uuid, text[], text, text, text, jsonb, text, text,
        text, integer, integer
    ) FROM mcp_app
    """,
    """
    REVOKE ALL ON FUNCTION audit.append(
        text, text, uuid, text[], text, text, text, jsonb, text, text, text, integer, integer
    ) FROM mcp_app, payments_worker
    """,
    "REVOKE ALL ON ALL TABLES IN SCHEMA core, control, audit FROM mcp_app, payments_worker",
    "REVOKE ALL ON SCHEMA core, control, audit FROM mcp_app, payments_worker",
)


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)

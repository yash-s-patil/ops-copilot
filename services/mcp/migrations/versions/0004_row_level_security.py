"""Row-level security on business tables. Fails closed when no region is set."""

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None

UPGRADE: tuple[str, ...] = (
    "ALTER TABLE core.customers ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE core.customers FORCE ROW LEVEL SECURITY",
    "ALTER TABLE core.orders ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE core.orders FORCE ROW LEVEL SECURITY",
    "ALTER TABLE core.tickets ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE core.tickets FORCE ROW LEVEL SECURITY",
    "ALTER TABLE core.refunds ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE core.refunds FORCE ROW LEVEL SECURITY",
    "ALTER TABLE core.order_events ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE core.order_events FORCE ROW LEVEL SECURITY",
    "ALTER TABLE core.ticket_notes ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE core.ticket_notes FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY region_scope ON core.customers
        FOR ALL TO mcp_app, migrator
        USING (
            region = (SELECT current_setting('app.region', true))
            OR (SELECT current_setting('app.region', true)) = '*'
        )
        WITH CHECK (
            region = (SELECT current_setting('app.region', true))
            OR (SELECT current_setting('app.region', true)) = '*'
        )
    """,
    """
    CREATE POLICY region_scope ON core.orders
        FOR ALL TO mcp_app, migrator
        USING (
            region = (SELECT current_setting('app.region', true))
            OR (SELECT current_setting('app.region', true)) = '*'
        )
        WITH CHECK (
            region = (SELECT current_setting('app.region', true))
            OR (SELECT current_setting('app.region', true)) = '*'
        )
    """,
    """
    CREATE POLICY region_scope ON core.tickets
        FOR ALL TO mcp_app, migrator
        USING (
            region = (SELECT current_setting('app.region', true))
            OR (SELECT current_setting('app.region', true)) = '*'
        )
        WITH CHECK (
            region = (SELECT current_setting('app.region', true))
            OR (SELECT current_setting('app.region', true)) = '*'
        )
    """,
    """
    CREATE POLICY region_scope ON core.refunds
        FOR ALL TO mcp_app, migrator
        USING (
            region = (SELECT current_setting('app.region', true))
            OR (SELECT current_setting('app.region', true)) = '*'
        )
        WITH CHECK (
            region = (SELECT current_setting('app.region', true))
            OR (SELECT current_setting('app.region', true)) = '*'
        )
    """,
    """
    CREATE POLICY order_scope ON core.order_events
        FOR ALL TO mcp_app, migrator
        USING (EXISTS (SELECT 1 FROM core.orders o WHERE o.id = order_events.order_id))
        WITH CHECK (EXISTS (SELECT 1 FROM core.orders o WHERE o.id = order_events.order_id))
    """,
    """
    CREATE POLICY ticket_scope ON core.ticket_notes
        FOR ALL TO mcp_app, migrator
        USING (EXISTS (SELECT 1 FROM core.tickets t WHERE t.id = ticket_notes.ticket_id))
        WITH CHECK (EXISTS (SELECT 1 FROM core.tickets t WHERE t.id = ticket_notes.ticket_id))
    """,
    "CREATE POLICY payments_read ON core.refunds FOR SELECT TO payments_worker USING (true)",
    """
    CREATE POLICY payments_settle ON core.refunds
        FOR UPDATE TO payments_worker
        USING (true)
        WITH CHECK (true)
    """,
    """
    CREATE POLICY payments_system_note ON core.ticket_notes
        FOR INSERT TO payments_worker
        WITH CHECK (author_kind = 'system')
    """,
)

DOWNGRADE: tuple[str, ...] = (
    "DROP POLICY payments_system_note ON core.ticket_notes",
    "DROP POLICY payments_settle ON core.refunds",
    "DROP POLICY payments_read ON core.refunds",
    "DROP POLICY ticket_scope ON core.ticket_notes",
    "DROP POLICY order_scope ON core.order_events",
    "DROP POLICY region_scope ON core.refunds",
    "DROP POLICY region_scope ON core.tickets",
    "DROP POLICY region_scope ON core.orders",
    "DROP POLICY region_scope ON core.customers",
    "ALTER TABLE core.ticket_notes NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE core.ticket_notes DISABLE ROW LEVEL SECURITY",
    "ALTER TABLE core.order_events NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE core.order_events DISABLE ROW LEVEL SECURITY",
    "ALTER TABLE core.refunds NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE core.refunds DISABLE ROW LEVEL SECURITY",
    "ALTER TABLE core.tickets NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE core.tickets DISABLE ROW LEVEL SECURITY",
    "ALTER TABLE core.orders NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE core.orders DISABLE ROW LEVEL SECURITY",
    "ALTER TABLE core.customers NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE core.customers DISABLE ROW LEVEL SECURITY",
)


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)

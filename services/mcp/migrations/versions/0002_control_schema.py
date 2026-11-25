"""Approval, outbox, rate-limit and chaos tables in the control schema."""

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None

UPGRADE: tuple[str, ...] = (
    "CREATE SCHEMA control",
    """
    CREATE TABLE control.pending_actions (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tool text NOT NULL,
        args jsonb NOT NULL,
        args_hash text NOT NULL,
        requester_oid uuid NOT NULL,
        requester_name text NOT NULL,
        requester_region text NOT NULL,
        approval_kind text NOT NULL,
        status text NOT NULL DEFAULT 'proposed',
        approver_oid uuid,
        approver_name text,
        decided_at timestamptz,
        decision_reason text,
        executed_at timestamptz,
        result jsonb,
        policy_snapshot jsonb,
        idempotency_key text NOT NULL,
        ticket_id bigint REFERENCES core.tickets (id),
        run_id text,
        trace_id text,
        expires_at timestamptz NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT pending_actions_idempotency_key UNIQUE (requester_oid, tool, idempotency_key),
        CONSTRAINT pending_actions_tool_check CHECK (tool IN ('update_ticket', 'issue_refund')),
        CONSTRAINT pending_actions_requester_region_check CHECK (
            requester_region IN ('west', 'south', '*')
        ),
        CONSTRAINT pending_actions_approval_kind_check CHECK (
            approval_kind IN ('self', 'supervisor')
        ),
        CONSTRAINT pending_actions_status_check CHECK (
            status IN ('proposed', 'approved', 'executed', 'rejected', 'expired', 'failed')
        ),
        CONSTRAINT pending_actions_decision_complete_check CHECK (
            (decided_at IS NULL) = (approver_oid IS NULL)
            AND (decided_at IS NULL) = (approver_name IS NULL)
        ),
        CONSTRAINT pending_actions_executed_at_check CHECK (
            (status = 'executed') = (executed_at IS NOT NULL)
        )
    )
    """,
    "CREATE INDEX pending_actions_ticket_id_idx ON control.pending_actions (ticket_id)",
    """
    CREATE TABLE control.approval_token_uses (
        jti uuid PRIMARY KEY,
        pending_action_id uuid NOT NULL REFERENCES control.pending_actions (id),
        used_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE INDEX approval_token_uses_pending_action_id_idx
        ON control.approval_token_uses (pending_action_id)
    """,
    """
    CREATE TABLE control.outbox (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        kind text NOT NULL,
        refund_id bigint NOT NULL REFERENCES core.refunds (id),
        payload jsonb NOT NULL,
        status text NOT NULL DEFAULT 'pending',
        attempts integer NOT NULL DEFAULT 0,
        next_attempt_at timestamptz NOT NULL DEFAULT now(),
        last_error text,
        trace_id text,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT outbox_kind_check CHECK (kind IN ('refund.submit')),
        CONSTRAINT outbox_status_check CHECK (
            status IN ('pending', 'submitted', 'done', 'failed')
        ),
        CONSTRAINT outbox_attempts_check CHECK (attempts >= 0)
    )
    """,
    "CREATE INDEX outbox_status_next_attempt_at_idx ON control.outbox (status, next_attempt_at)",
    "CREATE INDEX outbox_refund_id_idx ON control.outbox (refund_id)",
    """
    CREATE TABLE control.rate_limits (
        user_oid uuid PRIMARY KEY,
        tokens numeric NOT NULL,
        refilled_at timestamptz NOT NULL,
        CONSTRAINT rate_limits_tokens_check CHECK (tokens >= 0)
    )
    """,
    """
    CREATE TABLE control.chaos_flags (
        name text PRIMARY KEY,
        enabled boolean NOT NULL DEFAULT false,
        value jsonb,
        updated_by text,
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    ALTER TABLE core.refunds
        ADD CONSTRAINT refunds_pending_action_id_fkey
        FOREIGN KEY (pending_action_id) REFERENCES control.pending_actions (id)
    """,
)

DOWNGRADE: tuple[str, ...] = (
    "ALTER TABLE core.refunds DROP CONSTRAINT refunds_pending_action_id_fkey",
    "DROP TABLE control.chaos_flags",
    "DROP TABLE control.rate_limits",
    "DROP TABLE control.outbox",
    "DROP TABLE control.approval_token_uses",
    "DROP TABLE control.pending_actions",
    "DROP SCHEMA control",
)


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)

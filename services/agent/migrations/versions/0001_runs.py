"""Runs, their events, model usage and guardrail events, and a schema for checkpoints.

The agent's role may add events and usage but not change or remove them. The checkpoint
tables are created by the LangGraph checkpointer itself, in a schema of its own.
"""

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None

UPGRADE: tuple[str, ...] = (
    "CREATE SCHEMA copilot",
    """
    CREATE TABLE copilot.runs (
        id uuid PRIMARY KEY,
        user_oid uuid NOT NULL,
        user_name text NOT NULL,
        roles text[] NOT NULL,
        region text,
        ticket_id bigint,
        order_id bigint,
        status text NOT NULL DEFAULT 'queued',
        message text NOT NULL,
        model text NOT NULL,
        tokens_in integer NOT NULL DEFAULT 0,
        tokens_out integer NOT NULL DEFAULT 0,
        tokens_cached integer NOT NULL DEFAULT 0,
        estimated_cost_inr numeric(12, 2),
        tool_calls integer NOT NULL DEFAULT 0,
        model_turns integer NOT NULL DEFAULT 0,
        elapsed_ms integer NOT NULL DEFAULT 0,
        result jsonb,
        stop_reason text,
        trace_id text,
        last_seq integer NOT NULL DEFAULT 0,
        attempt integer NOT NULL DEFAULT 0,
        lease_owner uuid,
        lease_until timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        finished_at timestamptz,
        CONSTRAINT runs_status_check CHECK (
            status IN (
                'queued', 'running', 'awaiting_approval', 'reauth_required',
                'completed', 'stopped', 'failed', 'cancelled'
            )
        ),
        CONSTRAINT runs_lease_complete_check CHECK (
            (lease_owner IS NULL) = (lease_until IS NULL)
        ),
        CONSTRAINT runs_finished_check CHECK (
            (finished_at IS NOT NULL)
            = (status IN ('completed', 'stopped', 'failed', 'cancelled'))
        )
    )
    """,
    "CREATE INDEX runs_user_created_idx ON copilot.runs (user_oid, created_at DESC)",
    """
    CREATE INDEX runs_ticket_created_idx ON copilot.runs (ticket_id, created_at DESC)
        WHERE ticket_id IS NOT NULL
    """,
    "CREATE INDEX runs_created_idx ON copilot.runs (created_at DESC)",
    """
    CREATE TABLE copilot.run_events (
        run_id uuid NOT NULL REFERENCES copilot.runs (id) ON DELETE CASCADE,
        seq integer NOT NULL,
        type text NOT NULL,
        key text NOT NULL,
        payload jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (run_id, seq),
        CONSTRAINT run_events_key UNIQUE (run_id, key),
        CONSTRAINT run_events_seq_check CHECK (seq > 0)
    )
    """,
    """
    CREATE TABLE copilot.run_actions (
        action_id uuid PRIMARY KEY,
        run_id uuid NOT NULL REFERENCES copilot.runs (id) ON DELETE CASCADE,
        call_id text NOT NULL,
        tool text NOT NULL,
        idempotency_key text NOT NULL,
        expires_at timestamptz NOT NULL,
        decision text,
        decided_by text,
        decision_reason text,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT run_actions_decision_check CHECK (decision IN ('approved', 'rejected')),
        CONSTRAINT run_actions_decided_by_check CHECK ((decision IS NULL) = (decided_by IS NULL))
    )
    """,
    "CREATE INDEX run_actions_run_id_idx ON copilot.run_actions (run_id)",
    """
    CREATE TABLE copilot.llm_calls (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        run_id uuid NOT NULL REFERENCES copilot.runs (id) ON DELETE CASCADE,
        node text NOT NULL,
        model text NOT NULL,
        tokens_in integer NOT NULL,
        tokens_out integer NOT NULL,
        tokens_cached integer NOT NULL,
        latency_ms integer NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX llm_calls_run_id_idx ON copilot.llm_calls (run_id)",
    """
    CREATE TABLE copilot.guardrail_events (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        run_id uuid NOT NULL REFERENCES copilot.runs (id) ON DELETE CASCADE,
        layer text NOT NULL,
        rule text NOT NULL,
        severity text NOT NULL,
        action text NOT NULL,
        detail text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT guardrail_events_severity_check CHECK (severity IN ('low', 'medium', 'high'))
    )
    """,
    "CREATE INDEX guardrail_events_run_id_idx ON copilot.guardrail_events (run_id)",
    "CREATE SCHEMA langgraph",
    "GRANT USAGE ON SCHEMA copilot TO agent_app",
    "GRANT SELECT, INSERT ON copilot.runs TO agent_app",
    """
    GRANT UPDATE (
        status, tokens_in, tokens_out, tokens_cached, estimated_cost_inr, tool_calls,
        model_turns, elapsed_ms, result, stop_reason, last_seq, attempt, lease_owner,
        lease_until, finished_at
    ) ON copilot.runs TO agent_app
    """,
    """
    GRANT SELECT, INSERT ON copilot.run_events, copilot.run_actions, copilot.llm_calls,
        copilot.guardrail_events TO agent_app
    """,
    "GRANT UPDATE (decision, decided_by, decision_reason) ON copilot.run_actions TO agent_app",
    "GRANT USAGE, CREATE ON SCHEMA langgraph TO agent_app",
)

DOWNGRADE: tuple[str, ...] = (
    "DROP SCHEMA langgraph CASCADE",
    "DROP TABLE copilot.guardrail_events",
    "DROP TABLE copilot.llm_calls",
    "DROP TABLE copilot.run_actions",
    "DROP TABLE copilot.run_events",
    "DROP TABLE copilot.runs",
    "DROP SCHEMA copilot",
)


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)

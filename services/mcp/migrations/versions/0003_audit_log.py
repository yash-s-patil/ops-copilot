"""Append-only audit log with a hash chain."""

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None

UPGRADE: tuple[str, ...] = (
    "ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC",
    "CREATE SCHEMA audit",
    """
    CREATE TABLE audit.audit_log (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        occurred_at timestamptz NOT NULL,
        written_by text NOT NULL,
        trace_id text,
        run_id text,
        actor_oid uuid,
        actor_roles text[] NOT NULL,
        actor_kind text NOT NULL,
        target text NOT NULL,
        action text NOT NULL,
        args_redacted jsonb NOT NULL,
        outcome text NOT NULL,
        error_code text,
        result_hash text,
        result_bytes integer,
        duration_ms integer,
        prev_hash text NOT NULL,
        row_hash text NOT NULL,
        CONSTRAINT audit_log_row_hash_key UNIQUE (row_hash),
        CONSTRAINT audit_log_prev_hash_key UNIQUE (prev_hash),
        CONSTRAINT audit_log_actor_kind_check CHECK (
            actor_kind IN ('user', 'synthetic', 'service')
        ),
        CONSTRAINT audit_log_action_check CHECK (
            action IN (
                'call', 'deny', 'propose', 'approve', 'reject', 'execute',
                'submit', 'settle', 'retry', 'chaos_change'
            )
        ),
        CONSTRAINT audit_log_outcome_check CHECK (
            outcome IN ('ok', 'denied', 'error', 'quarantined')
        )
    )
    """,
    "CREATE INDEX audit_log_run_id_idx ON audit.audit_log (run_id)",
    "CREATE INDEX audit_log_occurred_at_idx ON audit.audit_log (occurred_at DESC)",
    """
    CREATE FUNCTION audit.compute_row_hash(
        p_prev_hash text,
        p_written_by text,
        p_occurred_at timestamptz,
        p_trace_id text,
        p_run_id text,
        p_actor_oid uuid,
        p_actor_roles text[],
        p_actor_kind text,
        p_target text,
        p_action text,
        p_args_redacted jsonb,
        p_outcome text,
        p_error_code text,
        p_result_hash text,
        p_result_bytes integer,
        p_duration_ms integer
    ) RETURNS text
    LANGUAGE sql
    STABLE
    SET search_path = pg_catalog, pg_temp
    AS $$
        SELECT encode(
            sha256(
                convert_to(
                    p_prev_hash || jsonb_build_object(
                        'occurred_at',
                        to_char(p_occurred_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                        'written_by', p_written_by,
                        'trace_id', p_trace_id,
                        'run_id', p_run_id,
                        'actor_oid', p_actor_oid,
                        'actor_roles', p_actor_roles,
                        'actor_kind', p_actor_kind,
                        'target', p_target,
                        'action', p_action,
                        'args_redacted', p_args_redacted,
                        'outcome', p_outcome,
                        'error_code', p_error_code,
                        'result_hash', p_result_hash,
                        'result_bytes', p_result_bytes,
                        'duration_ms', p_duration_ms
                    )::text,
                    'UTF8'
                )
            ),
            'hex'
        )
    $$
    """,
    """
    CREATE FUNCTION audit.append(
        p_trace_id text,
        p_run_id text,
        p_actor_oid uuid,
        p_actor_roles text[],
        p_actor_kind text,
        p_target text,
        p_action text,
        p_args_redacted jsonb,
        p_outcome text,
        p_error_code text,
        p_result_hash text,
        p_result_bytes integer,
        p_duration_ms integer
    ) RETURNS bigint
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        c_chain_lock CONSTANT bigint := 4471000001;
        c_genesis_hash CONSTANT text := repeat('0', 64);
        v_occurred_at timestamptz;
        v_prev_hash text;
        v_id bigint;
    BEGIN
        PERFORM pg_advisory_xact_lock(c_chain_lock);
        v_occurred_at := clock_timestamp();

        SELECT row_hash INTO v_prev_hash FROM audit.audit_log ORDER BY id DESC LIMIT 1;
        v_prev_hash := coalesce(v_prev_hash, c_genesis_hash);

        INSERT INTO audit.audit_log (
            occurred_at, written_by, trace_id, run_id, actor_oid, actor_roles, actor_kind,
            target, action,
            args_redacted, outcome, error_code, result_hash, result_bytes, duration_ms,
            prev_hash, row_hash
        ) VALUES (
            v_occurred_at, session_user, p_trace_id, p_run_id, p_actor_oid, p_actor_roles,
            p_actor_kind,
            p_target, p_action, p_args_redacted, p_outcome, p_error_code, p_result_hash,
            p_result_bytes, p_duration_ms, v_prev_hash,
            audit.compute_row_hash(
                v_prev_hash, session_user, v_occurred_at, p_trace_id, p_run_id, p_actor_oid,
                p_actor_roles,
                p_actor_kind, p_target, p_action, p_args_redacted, p_outcome, p_error_code,
                p_result_hash, p_result_bytes, p_duration_ms
            )
        )
        RETURNING id INTO v_id;

        RETURN v_id;
    END
    $$
    """,
    """
    CREATE FUNCTION audit.reject_change() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        RAISE EXCEPTION 'audit.audit_log is append-only' USING ERRCODE = 'insufficient_privilege';
    END
    $$
    """,
    """
    CREATE TRIGGER audit_log_no_update_or_delete
        BEFORE UPDATE OR DELETE ON audit.audit_log
        FOR EACH ROW EXECUTE FUNCTION audit.reject_change()
    """,
    """
    CREATE TRIGGER audit_log_no_truncate
        BEFORE TRUNCATE ON audit.audit_log
        FOR EACH STATEMENT EXECUTE FUNCTION audit.reject_change()
    """,
)

DOWNGRADE: tuple[str, ...] = (
    "DROP TABLE audit.audit_log",
    "DROP FUNCTION audit.reject_change()",
    """
    DROP FUNCTION audit.append(
        text, text, uuid, text[], text, text, text, jsonb, text, text, text, integer, integer
    )
    """,
    """
    DROP FUNCTION audit.compute_row_hash(
        text, text, timestamptz, text, text, uuid, text[], text, text, text, jsonb, text, text,
        text, integer, integer
    )
    """,
    "DROP SCHEMA audit",
    "ALTER DEFAULT PRIVILEGES GRANT EXECUTE ON FUNCTIONS TO PUBLIC",
)


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)

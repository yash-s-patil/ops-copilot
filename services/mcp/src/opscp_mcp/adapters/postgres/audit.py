"""The audit log. Rows are appended by one database function, which chains their hashes."""

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from opscp_mcp.ports.controls import AuditEntry

_APPEND = text(
    """
    SELECT audit.append(
        :trace_id, :run_id, :actor_oid, :actor_roles, :actor_kind, :target, :action,
        CAST(:args_redacted AS jsonb), :outcome, :error_code, :result_hash, :result_bytes,
        :duration_ms
    )
    """
)
_BROKEN_LINKS = text(
    """
    SELECT id FROM (
        SELECT
            id,
            prev_hash,
            row_hash,
            lag(row_hash, 1, repeat('0', 64)) OVER (ORDER BY id) AS row_before,
            audit.compute_row_hash(
                prev_hash, written_by, occurred_at, trace_id, run_id, actor_oid, actor_roles,
                actor_kind, target, action, args_redacted, outcome, error_code, result_hash,
                result_bytes, duration_ms
            ) AS recomputed
        FROM audit.audit_log
    ) AS chain
    WHERE prev_hash <> row_before OR row_hash <> recomputed
    ORDER BY id
    LIMIT :limit
    """
)
_COUNT = text("SELECT count(*) FROM audit.audit_log")


class PostgresAuditTrail:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def record(self, entry: AuditEntry) -> int:
        row = entry.model_dump() | {
            "actor_roles": list(entry.actor_roles),
            "args_redacted": json.dumps(entry.args_redacted),
        }
        async with self._engine.begin() as connection:
            return (await connection.execute(_APPEND, row)).scalar_one()


async def broken_links(connection: AsyncConnection, *, limit: int) -> tuple[int, list[int]]:
    """How many rows the log holds, and the first rows that were changed or follow a gap.

    A row is reported when its content no longer gives its hash, or when it does not carry
    the hash of the row before it. Rows removed from the end leave nothing to compare, which
    is why every entry is also written to the log stream.
    """
    broken = (await connection.execute(_BROKEN_LINKS, {"limit": limit})).scalars().all()
    return (await connection.execute(_COUNT)).scalar_one(), list(broken)

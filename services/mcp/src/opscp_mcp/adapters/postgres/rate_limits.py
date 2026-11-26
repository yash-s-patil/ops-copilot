"""One token bucket per user, kept in the database so that every replica shares it."""

import math
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from opscp_mcp.adapters.postgres.session import Database
from opscp_mcp.ports.controls import Allowance

_SECONDS_PER_MINUTE = 60
_OPEN_BUCKET = text(
    """
    INSERT INTO control.rate_limits (user_oid, tokens, refilled_at)
    VALUES (:user_oid, :burst, clock_timestamp())
    ON CONFLICT (user_oid) DO NOTHING
    """
)
# The row is locked, so two requests from one user are counted one after the other. A
# request that waited for the lock may hold an earlier clock reading than the row it then
# reads, so time is never allowed to run backwards.
_TAKE = text(
    """
    UPDATE control.rate_limits AS bucket
    SET tokens = level.tokens - CASE WHEN level.tokens >= 1 THEN 1 ELSE 0 END,
        refilled_at = level.at
    FROM (
        SELECT
            user_oid,
            GREATEST(refilled_at, clock_timestamp()) AS at,
            LEAST(
                CAST(:burst AS numeric),
                tokens + GREATEST(EXTRACT(EPOCH FROM clock_timestamp() - refilled_at), 0)
                    * CAST(:per_minute AS numeric) / 60
            ) AS tokens
        FROM control.rate_limits
        WHERE user_oid = :user_oid
        FOR UPDATE
    ) AS level
    WHERE bucket.user_oid = level.user_oid
    RETURNING level.tokens
    """
)


class PostgresRateLimiter:
    def __init__(self, database: Database, *, per_minute: int, burst: int) -> None:
        self._database = database
        self._per_minute = per_minute
        self._burst = burst

    async def take(self, user_oid: UUID) -> Allowance:
        bucket = {"user_oid": user_oid, "burst": self._burst, "per_minute": self._per_minute}

        async def count(connection: AsyncConnection) -> float:
            await connection.execute(_OPEN_BUCKET, {"user_oid": user_oid, "burst": self._burst})
            return float((await connection.execute(_TAKE, bucket)).scalar_one())

        tokens = await self._database.write(count)
        if tokens >= 1:
            return Allowance(allowed=True, retry_after_s=0)
        wait_s = (1 - tokens) * _SECONDS_PER_MINUTE / self._per_minute
        return Allowance(allowed=False, retry_after_s=max(1, math.ceil(wait_s)))

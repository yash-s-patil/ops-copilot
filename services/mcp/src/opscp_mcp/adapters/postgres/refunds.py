"""Retrying a failed refund in PostgreSQL: one transaction, in the caller's scope."""

from collections.abc import Callable
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from opscp_common.vocabulary import PAYMENT_OUTCOME_UNKNOWN
from opscp_mcp.adapters.postgres.actions import LOCK_ONE_ORDER, lock_key, refund_case
from opscp_mcp.adapters.postgres.audit import append
from opscp_mcp.adapters.postgres.outbox import Payment, queue_payment
from opscp_mcp.adapters.postgres.session import SET_SCOPE, Database
from opscp_mcp.ports.controls import AuditEntry
from opscp_mcp.ports.meridian import Scope
from opscp_mcp.ports.refunds import MayRetry, RefundToRetry, Retried

_ORDER_OF = text("SELECT order_id FROM core.refunds WHERE id = :refund_id")
_HELD = text(
    """
    SELECT id, order_id, amount_minor, currency::text AS currency, reason_code, status,
           requested_by_oid = approved_by_oid AS self_approved,
           failure_reason IS NOT DISTINCT FROM :unknown AS outcome_unknown
    FROM core.refunds WHERE id = :refund_id FOR UPDATE
    """
)
_PAYABLE_AGAIN = text(
    "UPDATE core.refunds SET status = 'approved', updated_at = :now "
    "WHERE id = :refund_id AND status = 'failed'"
)


class PostgresRefundRetries:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def retry(
        self,
        scope: Scope,
        refund_id: int,
        now: datetime,
        may_retry: MayRetry,
        entry: Callable[[], AuditEntry],
    ) -> Retried | None:
        async def retry(connection: AsyncConnection) -> Retried | None:
            await connection.execute(
                SET_SCOPE, {"user_oid": str(scope.user_oid), "region": scope.region}
            )
            found = await connection.execute(_ORDER_OF, {"refund_id": refund_id})
            order_id = found.scalar_one_or_none()
            if order_id is None:
                return None
            # The same lock a refund request and an execution take: the order's refunds
            # cannot change between the check below and the write.
            await connection.execute(LOCK_ONE_ORDER, {"order": lock_key(order_id)})
            held = {"refund_id": refund_id, "unknown": PAYMENT_OUTCOME_UNKNOWN}
            row = (await connection.execute(_HELD, held)).mappings().one()
            case = await refund_case(connection, order_id, now, leaving_out=refund_id)
            if case is None:
                return None
            refund = RefundToRetry(
                id=row["id"],
                order_id=order_id,
                amount_minor=row["amount_minor"],
                reason_code=row["reason_code"],
                status=row["status"],
                self_approved=row["self_approved"],
                outcome_unknown=row["outcome_unknown"],
            )
            may_retry(refund, case)
            await connection.execute(_PAYABLE_AGAIN, {"refund_id": refund_id, "now": now})
            payment = Payment(
                order_id=order_id,
                amount_minor=row["amount_minor"],
                currency=row["currency"],
                reason_code=row["reason_code"],
            )
            await queue_payment(connection, refund_id, payment, now)
            return Retried(refund_id=refund_id, audit_id=await append(connection, entry()))

        return await self._database.write(retry)

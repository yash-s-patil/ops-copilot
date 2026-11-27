"""The row that tells the payments worker to pay a refund.

It is always written in the transaction that makes the refund payable, so there is never a
refund to pay without its instruction, nor an instruction without its refund.
"""

import json
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from opscp_mcp.ports.base import Record

REFUND_SUBMIT = "refund.submit"
_ADD = text(
    "INSERT INTO control.outbox (kind, refund_id, payload, next_attempt_at, created_at, "
    "updated_at) VALUES (:kind, :refund_id, CAST(:payload AS jsonb), :now, :now, :now)"
)


class Payment(Record):
    """What the worker is told. No name, address or contact detail."""

    order_id: int
    amount_minor: int
    currency: str
    reason_code: str


async def queue_payment(
    connection: AsyncConnection, refund_id: int, payment: Payment, now: datetime
) -> None:
    row = {
        "kind": REFUND_SUBMIT,
        "refund_id": refund_id,
        "payload": json.dumps(payment.model_dump(), sort_keys=True),
        "now": now,
    }
    await connection.execute(_ADD, row)

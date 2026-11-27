"""Sending a refund whose payment failed round again."""

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from opscp_common.vocabulary import RefundReason, RefundStatus
from opscp_mcp.ports.actions import RefundCase
from opscp_mcp.ports.base import Record
from opscp_mcp.ports.controls import AuditEntry
from opscp_mcp.ports.meridian import Scope


class RefundToRetry(Record):
    id: int
    order_id: int
    amount_minor: int
    reason_code: RefundReason
    status: RefundStatus
    outcome_unknown: bool
    """Nobody learned what the payment service did with it, so it has counted all along."""
    self_approved: bool
    """The person who asked for it also approved it: no supervisor has seen it."""


class Retried(Record):
    refund_id: int
    audit_id: int


type MayRetry = Callable[[RefundToRetry, RefundCase], None]
"""Raises the refusal when the refund may not be sent to payments again."""


class RefundRetries(Protocol):
    async def retry(
        self,
        scope: Scope,
        refund_id: int,
        now: datetime,
        may_retry: MayRetry,
        entry: Callable[[], AuditEntry],
    ) -> Retried | None:
        """Makes the refund payable again, with its instruction to pay and its audit row.

        All three are written together or not at all. None when the refund does not exist
        or is outside the scope. `may_retry` is asked while the order's refunds are held
        still, so what it is told cannot change before the write, and is told about the
        order without this refund.
        """
        ...

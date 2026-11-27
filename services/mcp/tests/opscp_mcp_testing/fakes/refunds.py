"""Retrying a failed refund in memory, with the same all-or-nothing rule."""

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

from opscp_common.vocabulary import PAYMENT_OUTCOME_UNKNOWN
from opscp_mcp.ports.controls import AuditEntry, AuditTrail
from opscp_mcp.ports.meridian import Scope
from opscp_mcp.ports.refunds import MayRetry, RefundToRetry, Retried
from opscp_mcp_testing.fakes.actions import FakeRefundCases
from opscp_mcp_testing.world import RefundRow, World


class FakeRefundRetries:
    def __init__(self, world: World, cases: FakeRefundCases, audit: AuditTrail) -> None:
        self._world = world
        self.queued: list[int] = []
        """The refunds an instruction to pay was written for, in order."""
        self._cases = cases
        self._audit = audit

    def _refund(self, refund_id: int) -> RefundRow | None:
        row = next((row for row in self._world.refunds if row.id == refund_id), None)
        return None if row is None else self._cases.changed.get(refund_id, row)

    def status_of(self, refund_id: int) -> str:
        refund = self._refund(refund_id)
        return "" if refund is None else refund.status

    async def retry(
        self,
        scope: Scope,
        refund_id: int,
        now: datetime,
        may_retry: MayRetry,
        entry: Callable[[], AuditEntry],
    ) -> Retried | None:
        refund = self._refund(refund_id)
        if refund is None or scope.region not in ("*", refund.region):
            return None
        case = await self._cases.for_order(scope, refund.order_id, now=now, leaving_out=refund.id)
        if case is None:
            return None
        may_retry(
            RefundToRetry(
                id=refund.id,
                order_id=refund.order_id,
                amount_minor=refund.amount_minor,
                reason_code=refund.reason_code,
                status=refund.status,
                self_approved=refund.requested_by_name == refund.approved_by_name,
                outcome_unknown=refund.failure_reason == PAYMENT_OUTCOME_UNKNOWN,
            ),
            case,
        )
        audit_id = await self._audit.record(entry())
        self._cases.changed[refund_id] = replace(refund, status="approved")
        self.queued.append(refund_id)
        return Retried(refund_id=refund_id, audit_id=audit_id)

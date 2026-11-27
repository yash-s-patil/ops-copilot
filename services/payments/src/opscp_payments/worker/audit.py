"""The audit entry for one thing the worker did to a refund."""

import time

from opscp_payments.ports.outbox import AuditAction, AuditEntry, Handoff

SUBMIT_TARGET = "POST /refunds"
SETTLE_TARGET = "GET /refunds/{provider_ref}"
UNAVAILABLE_CODE = "PAYMENT_SERVICE_UNAVAILABLE"
DECLINED_CODE = "PAYMENT_DECLINED"
_TARGETS: dict[AuditAction, str] = {"submit": SUBMIT_TARGET, "settle": SETTLE_TARGET}
_MS_PER_S = 1000


def entry_for(
    handoff: Handoff,
    action: AuditAction,
    started: float,
    *,
    provider_ref: str | None,
    error_code: str | None = None,
) -> AuditEntry:
    """`started` is a reading of the monotonic clock taken when the step began."""
    args: dict[str, int | str] = {
        "refund_id": handoff.refund_id,
        "order_id": handoff.order_id,
        "outbox_id": str(handoff.outbox_id),
        "attempt": handoff.attempts + 1,
    }
    if provider_ref is not None:
        args["provider_ref"] = provider_ref
    return AuditEntry(
        trace_id=handoff.trace_id,
        target=_TARGETS[action],
        action=action,
        args=args,
        outcome="ok" if error_code is None else "error",
        error_code=error_code,
        duration_ms=round((time.monotonic() - started) * _MS_PER_S),
    )

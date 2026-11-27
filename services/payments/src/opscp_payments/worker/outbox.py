"""What the worker does with one outbox row: submit the refund, or ask how it ended.

Each row is worked on inside one transaction that also holds it, so two workers never take
the same row. The provider is called inside that transaction. If the worker stops between
the call and the commit, the row is as it was and the next attempt repeats the call with the
same idempotency key, which the provider answers with the first result.
"""

import random
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from functools import partial
from uuid import UUID

import structlog

from opscp_common.limits import PaymentsLimits
from opscp_payments.ports.outbox import AuditAction, AuditEntry, Handoff, Outbox, Step
from opscp_payments.ports.payments import (
    PaymentProvider,
    ProviderUnavailableError,
    Submission,
    Trouble,
)
from opscp_payments.worker.audit import DECLINED_CODE, UNAVAILABLE_CODE, entry_for
from opscp_payments.worker.refunds import (
    FOLLOW_UP_NOTE,
    SERVICE_UNAVAILABLE,
    paid_note,
    reason_from,
)

_LOG = structlog.get_logger(__name__)
_HALF = 0.5
# After any other trouble the provider may have taken the submission, drill and all.
_NEVER_ARRIVED: tuple[Trouble, ...] = ("unreachable", "no_token")

type Clock = Callable[[], datetime]
type Recorded = list[tuple[int, AuditEntry]]


class Handoffs:
    def __init__(
        self,
        outbox: Outbox,
        provider: PaymentProvider,
        limits: PaymentsLimits,
        clock: Clock,
        *,
        chaos_enabled: bool,
        spread: Callable[[], float] = random.random,
    ) -> None:
        """`spread` gives a number from 0 to 1 that spaces retries out. Not a secret."""
        self._outbox = outbox
        self._provider = provider
        self._limits = limits
        self._clock = clock
        self._chaos_enabled = chaos_enabled
        self._spread = spread

    async def poll(self) -> int:
        """Works on every row that is due, one transaction each. Returns how many it held."""
        held = 0
        for outbox_id in await self._outbox.due(self._clock(), self._limits.claim_batch):
            recorded: Recorded = []
            try:
                held += await self._outbox.step(
                    outbox_id, self._clock(), partial(self._work, recorded)
                )
            except Exception as error:  # noqa: BLE001 -- one row's failure must not stop the others
                # The row is as it was. The error's text may carry connection details, so
                # only its kind is logged.
                _LOG.error("handoff_failed", outbox_id=str(outbox_id), error=type(error).__name__)
                await self._set_aside(outbox_id)
                continue
            for audit_id, entry in recorded:
                _LOG.info("audit", audit_id=audit_id, **entry.model_dump(mode="json"))
        return held

    async def _set_aside(self, outbox_id: UUID) -> None:
        """A row that breaks every time must not be taken every poll, ahead of the others."""
        until = self._clock() + timedelta(seconds=self._limits.backoff_max_s)
        try:
            await self._outbox.set_aside(outbox_id, until)
        except Exception as error:  # noqa: BLE001 -- the database may be what is down
            _LOG.error("not_set_aside", outbox_id=str(outbox_id), error=type(error).__name__)

    async def _work(self, recorded: Recorded, handoff: Handoff, step: Step) -> None:
        if handoff.status == "pending":
            await self._submit(recorded, handoff, step)
        else:
            await self._settle(recorded, handoff, step)

    async def _submit(self, recorded: Recorded, handoff: Handoff, step: Step) -> None:
        started = time.monotonic()
        forced = self._chaos_enabled and await step.take_forced_decline()
        submission = Submission(
            refund_id=handoff.refund_id,
            order_id=handoff.order_id,
            amount_minor=handoff.amount_minor,
            currency=handoff.currency,
            forced_outcome="decline" if forced else None,
        )
        try:
            accepted = await self._provider.submit(submission)
        except ProviderUnavailableError as error:
            if forced and error.trouble in _NEVER_ARRIVED:
                await step.return_forced_decline()
            await self._try_later(recorded, handoff, step, "submit", error.trouble, started)
            return
        entry = entry_for(handoff, "submit", started, provider_ref=accepted.provider_ref)
        recorded.append((await step.submitted(accepted.provider_ref, entry), entry))

    async def _settle(self, recorded: Recorded, handoff: Handoff, step: Step) -> None:
        started = time.monotonic()
        provider_ref = handoff.provider_ref
        if provider_ref is None:
            raise RuntimeError("A submitted outbox row has a refund with no provider reference")
        try:
            settlement = await self._provider.status(provider_ref)
        except ProviderUnavailableError as error:
            await self._try_later(recorded, handoff, step, "settle", error.trouble, started)
            return
        if settlement.status == "processing":
            wait = timedelta(seconds=self._limits.poll_interval_s)
            await step.later(self._clock() + wait, trouble=None)
            return
        if settlement.status == "succeeded":
            entry = entry_for(handoff, "settle", started, provider_ref=provider_ref)
            recorded.append((await step.paid(paid_note(handoff), entry), entry))
            return
        entry = entry_for(
            handoff, "settle", started, provider_ref=provider_ref, error_code=DECLINED_CODE
        )
        reason = reason_from(settlement.reason_code)
        recorded.append((await step.failed(reason, reason, FOLLOW_UP_NOTE, entry), entry))

    async def _try_later(
        self,
        recorded: Recorded,
        handoff: Handoff,
        step: Step,
        action: AuditAction,
        trouble: Trouble,
        started: float,
    ) -> None:
        attempt = handoff.attempts + 1
        _LOG.warning(
            "provider_unavailable",
            refund_id=handoff.refund_id,
            action=action,
            trouble=trouble,
            attempt=attempt,
        )
        if attempt < self._limits.max_attempts:
            await step.later(self._clock() + self._backoff(attempt), trouble=trouble)
            return
        entry = entry_for(
            handoff,
            action,
            started,
            provider_ref=handoff.provider_ref,
            error_code=UNAVAILABLE_CODE,
        )
        audit_id = await step.failed(SERVICE_UNAVAILABLE, trouble, FOLLOW_UP_NOTE, entry)
        recorded.append((audit_id, entry))

    def _backoff(self, attempt: int) -> timedelta:
        """Doubles with each attempt up to the cap; the second half of each wait is spread."""
        limits = self._limits
        ceiling = min(limits.backoff_max_s, limits.backoff_base_s * 2 ** (attempt - 1))
        return timedelta(seconds=ceiling * (_HALF + _HALF * self._spread()))

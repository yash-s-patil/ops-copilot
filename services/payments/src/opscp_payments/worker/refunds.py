"""The words the worker leaves on a refund and its ticket."""

import re

from opscp_common.vocabulary import PAYMENT_OUTCOME_UNKNOWN
from opscp_payments.ports.outbox import Handoff

SERVICE_UNAVAILABLE = PAYMENT_OUTCOME_UNKNOWN
DECLINED = "declined"
FOLLOW_UP_NOTE = "Refund payment failed. Needs follow-up by an L2 engineer."
_MINOR_UNITS = 100
_REASON_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")


def money(amount_minor: int, currency: str) -> str:
    return f"{currency} {amount_minor // _MINOR_UNITS:,}.{amount_minor % _MINOR_UNITS:02d}"


def paid_note(handoff: Handoff) -> str:
    return f"Refund of {money(handoff.amount_minor, handoff.currency)} paid."


def reason_from(provider_reason: str | None) -> str:
    """The provider's reason if it is a plain code. Its free text is never stored or shown."""
    if provider_reason is not None and _REASON_CODE.fullmatch(provider_reason):
        return provider_reason
    return DECLINED

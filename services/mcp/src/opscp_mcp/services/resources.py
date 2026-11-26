"""Short reference texts an agent loads once and keeps until their version changes."""

import hashlib
from collections.abc import Mapping

import structlog

from opscp_common.errors import InternalError
from opscp_common.limits import Limits
from opscp_common.vocabulary import OrderStatus
from opscp_mcp.authz.identity import Identity
from opscp_mcp.authz.policy import Permission
from opscp_mcp.ports.knowledge import Runbooks
from opscp_mcp.services.caps import Result

ORDER_LIFECYCLE_URI = "meridian://reference/order-lifecycle"
REFUND_SUMMARY_URI = "meridian://policy/refund/summary"
RUNBOOK_INDEX_URI = "meridian://runbooks/index"

_LOG = structlog.get_logger(__name__)
_VERSION_HEX_CHARS = 12
_MINOR_UNITS_PER_RUPEE = 100

ORDER_MOVES: Mapping[OrderStatus, tuple[OrderStatus, ...]] = {
    "placed": ("paid", "cancelled"),
    "paid": ("packed", "cancelled"),
    "packed": ("shipped", "cancelled"),
    "shipped": ("delivered", "lost", "cancelled"),
    "delivered": (),
    "cancelled": (),
    "lost": (),
}


class Reference(Result):
    uri: str
    text: str
    version: str


def _reference(uri: str, text: str) -> Reference:
    digest = hashlib.sha256(text.encode()).hexdigest()
    return Reference(uri=uri, text=text, version=digest[:_VERSION_HEX_CHARS])


class ReferenceService:
    def __init__(self, runbooks: Runbooks, limits: Limits) -> None:
        self._runbooks = runbooks
        self._limits = limits

    def order_lifecycle(self, identity: Identity) -> Reference:
        identity.require(Permission.ORDERS_READ)
        stuck = self._limits.stuck
        moves = "\n".join(
            f"- {status} -> {', '.join(following)}" if following else f"- {status} is final"
            for status, following in ORDER_MOVES.items()
        )
        text = (
            "Order statuses and the moves allowed between them:\n"
            f"{moves}\n"
            "A shipped order is cancelled only after the parcel has come back to the warehouse.\n"
            "\n"
            "An order is stuck when it has been:\n"
            f"- paid for over {stuck.paid_hours} hours without being packed;\n"
            f"- packed for over {stuck.packed_hours} hours without a carrier pickup;\n"
            f"- shipped with no new carrier scan for over {stuck.shipped_no_scan_hours} hours. "
            "A scan identical to the one before it is not a new scan.\n"
            "Stuck is worked out from the status and the events and is never stored. "
            "get_order reports it as is_stuck, with status_age_hours."
        )
        return _reference(ORDER_LIFECYCLE_URI, text)

    def refund_summary(self, identity: Identity) -> Reference:
        identity.require(Permission.POLICIES_READ)
        premium, standard = self._limits.goodwill.premium, self._limits.goodwill.standard
        self_limit = self._limits.approval.self_limit_minor // _MINOR_UNITS_PER_RUPEE
        text = (
            "Refund rules in brief. The words in brackets are section ids of the document "
            "refund-policy; read a section with get_policy_document for the full text.\n"
            "- [scope] Reason codes: goodwill_delay, lost_in_transit, cancelled, damaged. "
            "Only an L2 engineer or a supervisor may request a refund; an L1 engineer "
            "escalates with a recommendation.\n"
            "- [eligibility] The reason needs evidence on the order. Total refunds never "
            "exceed the order value.\n"
            "- [goodwill] Only while the order is delayed and not delivered. Premium customer: "
            f"delay of {premium.min_delay_hours} hours or more, up to {premium.max_percent}% of "
            f"the order value. Standard customer: {standard.min_delay_hours} hours or more, up to "
            f"{standard.max_percent}%. One goodwill refund per order.\n"
            "- [full-refund] Only when the carrier has confirmed loss in transit, the order was "
            "cancelled after payment, or damage is reported.\n"
            "- [approval-limits] Every refund needs a person's approval. Up to INR "
            f"{self_limit:,} the requesting L2 engineer may approve; above that, a supervisor "
            "who is not the requester.\n"
            "- [exclusions] No refund for an order that was delivered late, for delay the "
            "customer caused, or where no payment was captured."
        )
        return _reference(REFUND_SUMMARY_URI, text)

    async def runbook_index(self, identity: Identity) -> Reference:
        identity.require(Permission.RUNBOOKS_READ)
        entries = await self._runbooks.catalog()
        if len(entries) > self._limits.runbooks.index_max:
            _LOG.error("runbook_index_too_long", entries=len(entries))
            raise InternalError
        text = "\n".join(
            f"{entry.runbook_id} | {entry.title} | {entry.summary}" for entry in entries
        )
        return _reference(RUNBOOK_INDEX_URI, text)

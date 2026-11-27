"""Retry payment: a person sends a refund whose payment failed to payments again.

Nothing about the approved decision changes: the amount, the order and the key the provider
knows the refund by all stay the same, so a retry can neither pay more nor pay twice. That
is why it needs no new approval. It is a route behind a button and never a tool.
"""

from functools import partial

from opscp_common.errors import NotFoundError, PolicyViolationError
from opscp_common.limits import ApprovalLimits
from opscp_common.vocabulary import GOODWILL_DELAY, REFUND_FAILED, SUPERVISOR_APPROVAL
from opscp_mcp.authz.identity import Identity
from opscp_mcp.authz.policy import Permission
from opscp_mcp.ports.actions import RefundCase
from opscp_mcp.ports.base import Record
from opscp_mcp.ports.refunds import RefundRetries, RefundToRetry
from opscp_mcp.services import refund_policy
from opscp_mcp.services.approvals import EntryNow, Recorded
from opscp_mcp.services.orders import Clock
from opscp_mcp.services.scope import scope_of

RETRIED_AS = "approved"


class RefundRetried(Record):
    refund_id: int
    status: str


def may_retry(limits: ApprovalLimits, refund: RefundToRetry, case: RefundCase) -> None:
    """Whether the refund still stands beside what has happened on its order since.

    `case` is the order without this refund. A refund whose payment is known to have failed
    does not count against its order, so others may have been asked for and made meanwhile.
    """
    if refund.status != REFUND_FAILED:
        raise PolicyViolationError(
            "Only a refund whose payment failed can be sent to payments again.",
            rule="refund_not_failed",
        )
    if case.request_waiting:
        refund_policy.refuse_while_waiting(refund.amount_minor)
    remaining = case.amount_minor - case.refunded_minor
    if refund.amount_minor > remaining:
        raise PolicyViolationError(
            "Other refunds have been made on this order since. This one no longer fits.",
            rule="exceeds_remaining",
            cap_minor=remaining,
            requested_minor=refund.amount_minor,
        )
    if refund.reason_code == GOODWILL_DELAY and case.goodwill_given:
        raise PolicyViolationError(
            "A goodwill refund has already been made on this order.",
            rule="goodwill_already_given",
        )
    # A refund whose outcome is unknown has counted against the order all along, so whatever
    # was approved after it was judged with it in the total. Only one that was left out,
    # because its payment was declined, can have been overtaken.
    needed = refund_policy.approval_kind(refund.amount_minor, case, limits)
    overtaken = needed == SUPERVISOR_APPROVAL and not refund.outcome_unknown
    if overtaken and refund.self_approved:
        raise PolicyViolationError(
            "With the refunds made since, this one needs a supervisor. Ask for it again.",
            rule="needs_supervisor",
        )


class RefundService:
    def __init__(self, retries: RefundRetries, limits: ApprovalLimits, clock: Clock) -> None:
        self._retries = retries
        self._limits = limits
        self._clock = clock

    async def retry(
        self, identity: Identity, refund_id: int, entry: EntryNow
    ) -> Recorded[RefundRetried]:
        identity.require(Permission.REFUNDS_REQUEST)
        retried = await self._retries.retry(
            scope_of(identity), refund_id, self._clock(), partial(may_retry, self._limits), entry
        )
        if retried is None:
            raise NotFoundError("No refund with that number is visible to you.")
        return Recorded(
            outcome=RefundRetried(refund_id=retried.refund_id, status=RETRIED_AS),
            audit_id=retried.audit_id,
        )

"""The route behind the Retry payment button. No tool can reach it."""

from typing import Annotated

from fastapi import APIRouter, Path, Request

from opscp_mcp.rest.approvals import Answered, Decision, SignedIn, decided, services_of
from opscp_mcp.services.refunds import RefundRetried

REFUNDS_PATH = "/refunds"
_BIGGEST_ID = 2**53 - 1

router = APIRouter(prefix=REFUNDS_PATH, route_class=Answered)


@router.post("/{refund_id}/retry")
async def retry(
    request: Request, identity: SignedIn, refund_id: Annotated[int, Path(gt=0, le=_BIGGEST_ID)]
) -> RefundRetried:
    refunds = services_of(request).refunds
    decision = Decision("retry", "POST /refunds/{id}/retry", {"refund_id": refund_id})
    return await decided(
        request, identity, decision, lambda entry: refunds.retry(identity, refund_id, entry)
    )

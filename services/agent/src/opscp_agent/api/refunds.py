"""The route behind the Retry payment button. It is sent on to the MCP server as it is."""

from typing import Annotated, Any

from fastapi import APIRouter, Path, Request

from opscp_agent.api.access import BIGGEST_ID, Answered, SignedIn, agent_of

router = APIRouter(prefix="/api/refunds", route_class=Answered)


@router.post("/{refund_id}/retry")
async def retry(
    request: Request, person: SignedIn, refund_id: Annotated[int, Path(gt=0, le=BIGGEST_ID)]
) -> dict[str, Any]:
    async with agent_of(request).gateway.session(person.token) as mcp:
        return await mcp.retry_refund(refund_id)

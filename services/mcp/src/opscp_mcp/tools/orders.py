from typing import Annotated

from fastmcp import Context, FastMCP
from pydantic import Field

from opscp_common.limits import Limits
from opscp_mcp.services.orders import EventDetail, OrderView
from opscp_mcp.tools.models import ToolOutput
from opscp_mcp.tools.runner import answer
from opscp_mcp.tools.shared import READ_ONLY, UNTRUSTED_TEXT

GET_ORDER = "get_order"


def register(server: FastMCP, limits: Limits) -> None:
    @server.tool(
        name=GET_ORDER,
        title="Read an order",
        annotations=READ_ONLY,
        description=(
            "Read one order: status, hours in that status, whether it is stuck, amount, "
            "amount already refunded, carrier and tracking reference. With events='recent' "
            f"it also returns the {limits.events.recent} most recent events, the total number "
            "of events and a count by type of the older ones. Use events='none' when only the "
            f"status is needed. {UNTRUSTED_TEXT}"
        ),
    )
    async def get_order(
        ctx: Context,
        order_id: Annotated[int, Field(description="The order number.")],
        events: Annotated[
            EventDetail, Field(description="'recent' to include events, 'none' to leave them out.")
        ] = "recent",
    ) -> ToolOutput[OrderView]:
        return await answer(
            ctx,
            GET_ORDER,
            limits.tool_timeout.get_order_s,
            lambda services, identity: services.orders.get(identity, order_id, events=events),
        )

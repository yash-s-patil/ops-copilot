"""The prompts. The texts themselves live in the service layer."""

from typing import Annotated

from fastmcp import Context, FastMCP
from pydantic import Field

from opscp_mcp.authz.policy import Permission
from opscp_mcp.services import prompts
from opscp_mcp.services.prompts import Tone
from opscp_mcp.tools.runner import services_of

INVESTIGATE_ORDER = "investigate_order"
DRAFT_CUSTOMER_REPLY = "draft_customer_reply"


def register(server: FastMCP) -> None:
    @server.prompt(
        name=INVESTIGATE_ORDER,
        title="Investigate an order",
        description="The standard procedure for finding why an order is stuck and what to do.",
    )
    def investigate_order(
        ctx: Context,
        order_id: Annotated[int, Field(description="The order number.")],
        ticket_id: Annotated[
            int | None, Field(description="The ticket about the order, when known.")
        ] = None,
    ) -> str:
        services_of(ctx).identities.current().require(Permission.ORDERS_READ)
        return prompts.investigate_order(order_id, ticket_id)

    @server.prompt(
        name=DRAFT_CUSTOMER_REPLY,
        title="Draft a customer reply",
        description="Meridian's voice rules and required statements for a reply to a customer.",
    )
    def draft_customer_reply(
        ctx: Context,
        ticket_id: Annotated[int, Field(description="The ticket being answered.")],
        tone: Annotated[Tone, Field(description="neutral, apologetic or firm.")],
    ) -> str:
        services_of(ctx).identities.current().require(Permission.TICKETS_READ)
        return prompts.draft_customer_reply(ticket_id, tone)

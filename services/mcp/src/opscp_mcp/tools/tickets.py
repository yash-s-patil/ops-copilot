from typing import Annotated

from fastmcp import Context, FastMCP
from pydantic import Field

from opscp_common.limits import Limits
from opscp_common.vocabulary import TicketStatus
from opscp_mcp.authz.policy import Permission
from opscp_mcp.services.queues import View
from opscp_mcp.services.tickets import TicketPage, TicketQuery, TicketView
from opscp_mcp.tools.models import ToolOutput
from opscp_mcp.tools.names import GET_TICKET, LIST_TICKETS
from opscp_mcp.tools.runner import answer
from opscp_mcp.tools.shared import READ_ONLY, UNTRUSTED_TEXT

REQUIRES = {LIST_TICKETS: Permission.TICKETS_READ, GET_TICKET: Permission.TICKETS_READ}


def register(server: FastMCP, limits: Limits) -> None:
    @server.tool(
        name=LIST_TICKETS,
        title="List tickets",
        annotations=READ_ONLY,
        description=(
            "List tickets, most recently updated first. Give a view, or an order_id or "
            "customer_id; there is no way to list everything. The views are my_queue, all_open, "
            "waiting and done, and what each holds depends on the caller's role. Returns one "
            f"page (default {limits.tickets.page_size_default}, at most "
            f"{limits.tickets.page_size_max}), next_cursor for the following page, and counts: "
            f"the size of each view. {UNTRUSTED_TEXT}"
        ),
    )
    async def list_tickets(  # noqa: PLR0913 -- the parameters are the tool's published schema
        ctx: Context,
        view: Annotated[View | None, Field(description="One of the four inbox views.")] = None,
        order_id: Annotated[int | None, Field(description="Only tickets about this order.")] = None,
        customer_id: Annotated[
            int | None, Field(description="Only tickets raised by this customer.")
        ] = None,
        status: Annotated[
            TicketStatus | None, Field(description="Only tickets with this status.")
        ] = None,
        cursor: Annotated[
            str | None, Field(description="next_cursor from the previous page, unchanged.")
        ] = None,
        page_size: Annotated[int | None, Field(description="How many tickets to return.")] = None,
    ) -> ToolOutput[TicketPage]:
        query = TicketQuery(
            view=view,
            order_id=order_id,
            customer_id=customer_id,
            status=status,
            cursor=cursor,
            page_size=page_size,
        )
        return await answer(
            ctx,
            LIST_TICKETS,
            limits.tool_timeout.list_tickets_s,
            lambda services, identity: services.tickets.list(identity, query),
        )

    @server.tool(
        name=GET_TICKET,
        title="Read a ticket",
        annotations=READ_ONLY,
        description=(
            "Read one ticket: status, priority, queue, summary, who escalated it and any refund "
            "they recommended, the refund on the ticket and its state, and the most recent "
            f"notes (default {limits.notes.default}, at most {limits.notes.max}; 0 for none). "
            f"{UNTRUSTED_TEXT}"
        ),
    )
    async def get_ticket(
        ctx: Context,
        ticket_id: Annotated[int, Field(description="The ticket number.")],
        notes: Annotated[int | None, Field(description="How many recent notes to include.")] = None,
    ) -> ToolOutput[TicketView]:
        return await answer(
            ctx,
            GET_TICKET,
            limits.tool_timeout.get_ticket_s,
            lambda services, identity: services.tickets.get(identity, ticket_id, notes=notes),
        )

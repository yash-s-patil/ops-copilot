"""The two tools that change something. Neither changes anything by itself being called.

A call without an approval records what is asked and answers "approval_required". Only a
person can approve, through a route that no tool reaches.
"""

from typing import Annotated

from fastmcp import Context, FastMCP
from pydantic import Field

from opscp_common.limits import Limits
from opscp_common.vocabulary import RefundReason, TicketStatus
from opscp_mcp.authz.policy import Permission
from opscp_mcp.services.proposals import (
    EscalateTo,
    Proposal,
    Recommendation,
    RefundRequest,
    TicketUpdate,
)
from opscp_mcp.tools.models import ToolOutput
from opscp_mcp.tools.names import ISSUE_REFUND, UPDATE_TICKET
from opscp_mcp.tools.runner import answer
from opscp_mcp.tools.shared import WRITE

REQUIRES = {
    UPDATE_TICKET: Permission.TICKETS_WRITE,
    ISSUE_REFUND: Permission.REFUNDS_REQUEST,
}
_NEEDS_APPROVAL = (
    "Nothing is changed by this call: it records the request and returns status "
    "'approval_required' with an action_id for a person to approve."
)
_KEY = "A new value for each change you intend, repeated unchanged on every retry of that change."
_TOKEN = "Leave unset. It is supplied after a person has approved the action."  # noqa: S105 -- a description


def register(server: FastMCP, limits: Limits) -> None:
    @server.tool(
        name=UPDATE_TICKET,
        title="Change a ticket",
        annotations=WRITE,
        description=(
            "Ask to change a ticket: set its status, add a note, or escalate it to L2. Give at "
            "least one of status, note or escalate_to. Status may move from open to "
            "pending_customer or resolved, from pending_customer to open or resolved, and from "
            "resolved to closed. Escalating needs a note, works only on an open ticket in the "
            "L1 queue, and may carry a refund recommendation, which is checked against the "
            f"refund policy. A note is at most {limits.ticket.note_write_chars} characters. "
            f"{_NEEDS_APPROVAL}"
        ),
    )
    async def update_ticket(  # noqa: PLR0913 -- the parameters are the tool's published schema
        ctx: Context,
        ticket_id: Annotated[int, Field(description="The ticket number.")],
        idempotency_key: Annotated[str, Field(description=_KEY)],
        status: Annotated[TicketStatus | None, Field(description="The new status.")] = None,
        note: Annotated[str | None, Field(description="A note to add to the ticket.")] = None,
        escalate_to: Annotated[
            EscalateTo | None, Field(description="'L2' to hand the ticket to the L2 queue.")
        ] = None,
        recommendation: Annotated[
            Recommendation | None,
            Field(description="With an escalation: the refund you suggest L2 considers."),
        ] = None,
        approval_token: Annotated[str | None, Field(description=_TOKEN)] = None,
    ) -> ToolOutput[Proposal]:
        update = TicketUpdate(
            ticket_id=ticket_id,
            status=status,
            note=note,
            escalate_to=escalate_to,
            recommendation=recommendation,
        )
        return await answer(
            ctx,
            UPDATE_TICKET,
            limits.tool_timeout.update_ticket_s,
            lambda services, identity: services.proposals.ticket_update(
                identity, update, idempotency_key=idempotency_key, approval_token=approval_token
            ),
        )

    @server.tool(
        name=ISSUE_REFUND,
        title="Request a refund",
        annotations=WRITE,
        description=(
            "Ask for a refund on an order. The amount is in paise. The reason must be one of "
            "goodwill_delay, lost_in_transit, cancelled or damaged, and the order must show the "
            "evidence the refund policy asks for; a refusal names the rule and the most that "
            f"could be asked for. reason_text is at most {limits.refund.reason_chars} "
            f"characters. {_NEEDS_APPROVAL}"
        ),
    )
    async def issue_refund(  # noqa: PLR0913 -- the parameters are the tool's published schema
        ctx: Context,
        order_id: Annotated[int, Field(description="The order number.")],
        amount_minor: Annotated[int, Field(description="The amount to refund, in paise.")],
        reason_code: Annotated[RefundReason, Field(description="Why the refund is due.")],
        reason_text: Annotated[
            str, Field(description="The reason in a sentence, with the evidence.")
        ],
        idempotency_key: Annotated[str, Field(description=_KEY)],
        ticket_id: Annotated[
            int | None, Field(description="The ticket about this order, when there is one.")
        ] = None,
        approval_token: Annotated[str | None, Field(description=_TOKEN)] = None,
    ) -> ToolOutput[Proposal]:
        request = RefundRequest(
            order_id=order_id,
            amount_minor=amount_minor,
            reason_code=reason_code,
            reason_text=reason_text,
            ticket_id=ticket_id,
        )
        return await answer(
            ctx,
            ISSUE_REFUND,
            limits.tool_timeout.issue_refund_s,
            lambda services, identity: services.proposals.refund(
                identity, request, idempotency_key=idempotency_key, approval_token=approval_token
            ),
        )

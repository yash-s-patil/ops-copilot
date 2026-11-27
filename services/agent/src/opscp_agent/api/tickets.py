"""The data behind the inbox and the ticket workspace.

Here the service is a plain client of the MCP server: it exchanges the caller's token and
calls the same tools a run calls. No model is involved and the service gains no rights, so
masking, row-level security, caps and audit apply as they do in a run.
"""

import asyncio
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Path, Query, Request

from opscp_agent.api.access import BIGGEST_ID, Answered, Shape, SignedIn, agent_of, data_of
from opscp_agent.jsonish import as_list, as_object
from opscp_agent.mcp_client import tool_adapter as tool
from opscp_agent.mcp_client.types import McpCallError, McpSession
from opscp_agent.runs.store import Run
from opscp_common.errors import ErrorCode, PolicyViolationError

router = APIRouter(prefix="/api/tickets", route_class=Answered)

type View = Literal["my_queue", "all_open", "waiting", "done"]
_CURSOR_CHARS_MAX = 400
TicketId = Annotated[int, Path(gt=0, le=BIGGEST_ID)]
RESOLVED = "resolved"
EXECUTED = "executed"
_CLICK = r"^[A-Za-z0-9_-]{8,56}$"


class Investigation(Shape):
    """One investigation on a ticket. Another person's is shown as a summary only."""

    run_id: UUID
    by_name: str
    mine: bool
    status: str
    started_at: datetime
    finished_at: datetime | None
    root_cause: str | None
    actions: list[dict[str, Any]]


class Workspace(Shape):
    ticket: dict[str, Any]
    order: dict[str, Any] | None
    customer: dict[str, Any] | None
    approvals: list[dict[str, Any]]
    investigations: list[Investigation]


class TicketStatus(Shape):
    ticket_id: int
    status: str
    queue: str
    refund_state: str | None
    version: int


class Resolved(Shape):
    ticket_id: int
    status: str
    version: int


def investigation(run: Run, viewer: UUID) -> Investigation:
    result = run.result or {}
    return Investigation(
        run_id=run.id,
        by_name=run.person.name,
        mine=run.person.oid == viewer,
        status=run.status,
        started_at=run.created_at,
        finished_at=run.finished_at,
        root_cause=result.get("root_cause"),
        actions=[
            {"tool": action.get("tool"), "status": action.get("status")}
            for action in result.get("proposed_actions", [])
        ],
    )


@router.get("")
async def inbox(
    request: Request,
    caller: SignedIn,
    view: View = "my_queue",
    cursor: Annotated[str | None, Query(max_length=_CURSOR_CHARS_MAX)] = None,
) -> dict[str, Any]:
    """One page of an inbox view, with the count for each view. One tool call."""
    agent = agent_of(request)
    asked: dict[str, Any] = {"view": view, "page_size": agent.limits.inbox.page_size}
    if cursor is not None:
        asked["cursor"] = cursor
    async with agent.gateway.session(caller.token) as mcp:
        return data_of(await mcp.call(tool.LIST_TICKETS, asked))


async def _optional(mcp: McpSession, name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
    """A part of the page that may be missing without the page failing."""
    outcome = await mcp.call(name, arguments)
    return None if outcome.error is not None else outcome.data


@router.get("/{ticket_id}", response_model=None)
async def workspace(
    request: Request,
    caller: SignedIn,
    ticket_id: TicketId,
    part: Literal["status"] | None = None,
) -> Workspace | TicketStatus:
    """Everything the ticket workspace shows, or with part=status only how it stands.

    What was investigated on the ticket is added only after the caller's own read of the
    ticket has succeeded, so a person who cannot see a ticket learns nothing found on it.
    """
    agent = agent_of(request)
    async with agent.gateway.session(caller.token) as mcp:
        if part is not None:
            ticket = data_of(await mcp.call(tool.GET_TICKET, {"ticket_id": ticket_id, "notes": 0}))
            return TicketStatus(
                ticket_id=ticket_id,
                status=ticket["status"],
                queue=ticket["queue"],
                refund_state=as_object(ticket.get("refund")).get("state"),
                version=ticket["version"],
            )
        notes = agent.limits.notes.max
        ticket = data_of(await mcp.call(tool.GET_TICKET, {"ticket_id": ticket_id, "notes": notes}))
        order, customer, waiting = await asyncio.gather(
            _optional(mcp, tool.GET_ORDER, {"order_id": ticket.get("order_id")}),
            _optional(mcp, tool.LOOKUP_CUSTOMER, {"customer_id": ticket.get("customer_id")}),
            mcp.approvals(ticket_id),
        )
    runs = await agent.store.on_ticket(ticket_id, agent.limits.runs.list_max)
    return Workspace(
        ticket=ticket,
        order=order,
        customer=customer,
        approvals=as_list(waiting.get("items")),
        investigations=[investigation(run, caller.person.oid) for run in runs],
    )


async def _approval_for(mcp: McpSession, recorded: dict[str, Any]) -> str | None:
    """The approval to carry a recorded resolve out with; None when it is already over.

    The server answers a repeated request with the action as first recorded, whether or
    not it has been approved since. So an approval that is refused because it was already
    given is followed by asking for the token of the approved action.
    """
    if recorded.get("status") != tool.APPROVAL_REQUIRED:
        return None
    action_id = UUID(str(recorded["action_id"]))
    try:
        approved = await mcp.approve(action_id, str(recorded["args_hash"]))
    except McpCallError as refused:
        if refused.body.code != ErrorCode.APPROVAL_INVALID:
            raise
        return await mcp.approval_token(action_id)
    given = as_object(approved.get("approval")).get("token")
    return given if isinstance(given, str) else None


@router.post("/{ticket_id}/resolve")
async def resolve(
    request: Request,
    caller: SignedIn,
    ticket_id: TicketId,
    idempotency_key: Annotated[str, Header(pattern=_CLICK)],
) -> Resolved:
    """Behind the Confirm resolve button, and nowhere else.

    The same three steps as any change: the request is recorded, the person who clicked
    approves it, and it is carried out with that approval. The audit log shows all three.
    The page sends one `Idempotency-Key` for one click and repeats it if it must ask
    again, so a repeat goes on from where the first attempt stopped and asks for nothing new.
    """
    agent = agent_of(request)
    asked: dict[str, Any] = {
        "ticket_id": ticket_id,
        "status": RESOLVED,
        tool.IDEMPOTENCY_KEY: f"resolve-{idempotency_key}",
    }
    async with agent.gateway.session(caller.token) as mcp:
        recorded = data_of(await mcp.call(tool.UPDATE_TICKET, asked))
        approval = await _approval_for(mcp, recorded)
        if isinstance(approval, str):
            recorded = data_of(
                await mcp.call(tool.UPDATE_TICKET, asked | {tool.APPROVAL_TOKEN: approval})
            )
    result = as_object(recorded.get("result"))
    if recorded.get("status") != EXECUTED or not result:
        raise PolicyViolationError(
            "The ticket could not be resolved. Open it again to see how it stands.",
            rule="not_resolved",
        )
    return Resolved(ticket_id=ticket_id, status=result["status"], version=result["version"])

"""Decisions made from a ticket, not from a run: a supervisor's, and what follows them.

A supervisor approves in their own inbox. That mints nothing anyone else can use. The
change is carried out when its requester returns, with the requester's own sign-in.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request

from opscp_agent.api.access import Answered, Shape, SignedIn, agent_of, data_of
from opscp_agent.api.runs import Approve, Reject, decision_noted
from opscp_agent.mcp_client.tool_adapter import APPROVAL_TOKEN, IDEMPOTENCY_KEY
from opscp_agent.runs.store import AWAITING_APPROVAL, CANCELLED
from opscp_common.errors import NotFoundError, PolicyViolationError

router = APIRouter(prefix="/api/approvals", route_class=Answered)


class Executed(Shape):
    action_id: UUID
    resumed_run: UUID | None
    result: dict[str, Any] | None


@router.post("/{action_id}/approve")
async def approve(
    request: Request, person: SignedIn, action_id: UUID, body: Approve
) -> dict[str, Any]:
    """Behind the Approve button of a supervisor's banner. Sent on with their own sign-in."""
    async with agent_of(request).gateway.session(person.token) as mcp:
        approved = await mcp.approve(action_id, body.args_hash)
    await decision_noted(request, action_id, "approved", person, None)
    return approved.get("action", {})


@router.post("/{action_id}/reject")
async def reject(
    request: Request, person: SignedIn, action_id: UUID, body: Reject
) -> dict[str, Any]:
    async with agent_of(request).gateway.session(person.token) as mcp:
        rejected = await mcp.reject(action_id, body.reason)
    await decision_noted(request, action_id, "rejected", person, body.reason)
    return rejected


@router.post("/{action_id}/execute")
async def execute(request: Request, person: SignedIn, action_id: UUID) -> Executed:
    """The requester carries out what a supervisor approved. No model is involved.

    If the run that asked for the change is still waiting, it goes on from where it
    stopped. Otherwise the change is made here, with the arguments the server stored and
    the key the run asked under.
    """
    agent = agent_of(request)
    asked = await agent.store.action(action_id)
    run = None if asked is None else await agent.store.get(asked.run_id)
    if asked is None or run is None or run.person.oid != person.person.oid:
        raise NotFoundError("No change with that id is yours to carry out.")
    if run.status == CANCELLED:
        raise PolicyViolationError(
            "That investigation was stopped, so its changes are not carried out.",
            rule="run_stopped",
        )
    if run.status == AWAITING_APPROVAL:
        resumed = await agent.manager.resume_if_decided(run.id, person, ask_server=True)
        if not resumed:
            raise PolicyViolationError(
                "This is still waiting for a decision.", rule="decision_open"
            )
        return Executed(action_id=action_id, resumed_run=run.id, result=None)
    async with agent.gateway.session(person.token, str(run.id)) as mcp:
        stored = await mcp.approval(action_id)
        approval = await mcp.approval_token(action_id)
        sent = dict(stored.get("args", {})) | {
            IDEMPOTENCY_KEY: asked.idempotency_key,
            APPROVAL_TOKEN: approval,
        }
        done = data_of(await mcp.call(asked.tool, sent))
    return Executed(action_id=action_id, resumed_run=None, result=done.get("result"))

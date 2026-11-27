"""Runs: starting one, following it, deciding on what it asks for, and stopping it.

A run belongs to the person who started it. To anyone else it does not exist.
"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse
from pydantic import Field

from opscp_agent.api.access import BIGGEST_ID, Answered, Body, Shape, SignedIn, agent_of
from opscp_agent.auth.verifier import Caller
from opscp_agent.events import types as events
from opscp_agent.events.sse import HEADERS, MEDIA_TYPE, EventStream
from opscp_agent.graph.context import Journal
from opscp_agent.runs.store import Decision, Run
from opscp_common.errors import NotFoundError

RUNS_PATH = "/api/runs"
router = APIRouter(prefix=RUNS_PATH, route_class=Answered)

_HASH = r"^[0-9a-f]{64}$"
_NO_RUN = "No investigation with that id is yours."


class StartRun(Body):
    # The length is checked against the limit after invisible characters are removed.
    message: str
    ticket_id: int | None = Field(default=None, gt=0, le=BIGGEST_ID)


class Started(Shape):
    run_id: UUID


class RunView(Shape):
    run_id: UUID
    ticket_id: int | None
    order_id: int | None
    status: str
    message: str
    started_at: datetime
    finished_at: datetime | None
    result: dict[str, Any] | None
    stop_reason: str | None


class RunList(Shape):
    items: list[RunView]


class Approve(Body):
    args_hash: str = Field(pattern=_HASH)


class Reject(Body):
    reason: str = Field(min_length=1)


class Decided(Shape):
    action_id: UUID
    decision: str
    resumed: bool


class Stopped(Shape):
    run_id: UUID
    stopped: bool


def run_view(run: Run) -> RunView:
    return RunView(
        run_id=run.id,
        ticket_id=run.ticket_id,
        order_id=run.order_id,
        status=run.status,
        message=run.message,
        started_at=run.created_at,
        finished_at=run.finished_at,
        result=run.result,
        stop_reason=run.stop_reason,
    )


async def own_run(request: Request, person: Caller, run_id: UUID) -> Run:
    run = await agent_of(request).store.get(run_id)
    if run is None or run.person.oid != person.person.oid:
        raise NotFoundError(_NO_RUN)
    return run


@router.post("", status_code=202)
async def start(request: Request, person: SignedIn, body: StartRun) -> Started:
    run_id = await agent_of(request).manager.start(person, body.message, body.ticket_id)
    return Started(run_id=run_id)


@router.get("")
async def recent(request: Request, person: SignedIn, ask: bool = False) -> RunList:
    """The caller's recent runs. With ask=true, only those that are not about a ticket."""
    agent = agent_of(request)
    runs = await agent.store.recent_for(
        person.person.oid, ask_only=ask, limit=agent.limits.runs.list_max
    )
    return RunList(items=[run_view(run) for run in runs])


@router.get("/{run_id}")
async def one(request: Request, person: SignedIn, run_id: UUID) -> RunView:
    run = await own_run(request, person, run_id)
    await agent_of(request).manager.nudge(run, person)
    return run_view(run)


async def decision_noted(
    request: Request, action_id: UUID, decision: Decision, person: Caller, reason: str | None
) -> None:
    """Notes a decision the server has taken, and tells whoever is following the run.

    Called only after the MCP server accepted the decision. A change no run asked for, such
    as one made from the Resolve button, has nothing to note.
    """
    agent = agent_of(request)
    asked = await agent.store.action(action_id)
    if asked is None:
        return
    await agent.store.note_decision(action_id, decision, person.person.name, reason)
    journal = Journal(agent.store, agent.bus, agent.cost, asked.run_id, 0)
    await journal.emit(
        events.APPROVAL_RESOLVED,
        f"approval.resolved:{action_id}",
        {
            "action_id": str(action_id),
            "decision": decision,
            "approver": person.person.name,
            "approval_kind": None,
        },
    )


async def _own_action(request: Request, person: Caller, run_id: UUID, action_id: UUID) -> Run:
    run = await own_run(request, person, run_id)
    action = await agent_of(request).store.action(action_id)
    if action is None or action.run_id != run.id:
        raise NotFoundError("That change is not part of this investigation.")
    return run


@router.post("/{run_id}/actions/{action_id}/approve")
async def approve(
    request: Request, person: SignedIn, run_id: UUID, action_id: UUID, body: Approve
) -> Decided:
    """Behind the Approve button of an approval card. The server decides whether it stands.

    The approval the server issues is not kept: when the run carries the change out it asks
    for one again, which the server gives only to the requester of an approved action.
    """
    run = await _own_action(request, person, run_id, action_id)
    agent = agent_of(request)
    async with agent.gateway.session(person.token, str(run.id)) as mcp:
        await mcp.approve(action_id, body.args_hash)
    await decision_noted(request, action_id, "approved", person, None)
    resumed = await agent.manager.resume_if_decided(run.id, person)
    return Decided(action_id=action_id, decision="approved", resumed=resumed)


@router.post("/{run_id}/actions/{action_id}/reject")
async def reject(
    request: Request, person: SignedIn, run_id: UUID, action_id: UUID, body: Reject
) -> Decided:
    run = await _own_action(request, person, run_id, action_id)
    agent = agent_of(request)
    async with agent.gateway.session(person.token, str(run.id)) as mcp:
        await mcp.reject(action_id, body.reason)
    await decision_noted(request, action_id, "rejected", person, body.reason)
    resumed = await agent.manager.resume_if_decided(run.id, person)
    return Decided(action_id=action_id, decision="rejected", resumed=resumed)


@router.post("/{run_id}/cancel")
async def cancel(request: Request, person: SignedIn, run_id: UUID) -> Stopped:
    run = await own_run(request, person, run_id)
    return Stopped(run_id=run.id, stopped=await agent_of(request).manager.cancel(run, person))


@router.get("/{run_id}/events", response_model=None)
async def follow(
    request: Request,
    person: SignedIn,
    run_id: UUID,
    last_event_id: Annotated[int | None, Header(ge=0)] = None,
) -> StreamingResponse:
    """The run's events as they happen. `Last-Event-ID` resumes after that event.

    Opening the stream is also how a run is picked up again after the process that was
    working on it has gone, or after its owner has signed in again.
    """
    run = await own_run(request, person, run_id)
    agent = agent_of(request)
    await agent.manager.nudge(run, person)
    stream = EventStream(agent.store, agent.bus, agent.limits.stream)
    return StreamingResponse(
        stream.of_run(run.id, last_event_id or 0, request.is_disconnected),
        media_type=MEDIA_TYPE,
        headers=HEADERS,
    )

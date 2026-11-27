"""What an administrator sees of runs: how they went, never what they were about.

These routes return the admin projection only. A run's message, its model text, its step
summaries and its results are not in it.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from opscp_agent.api.access import Admin, Answered, Shape, agent_of
from opscp_agent.events.admin_projection import admin_view
from opscp_agent.events.sse import HEADERS, MEDIA_TYPE, EventStream
from opscp_agent.runs.store import Run
from opscp_common.errors import NotFoundError

router = APIRouter(prefix="/api/admin", route_class=Answered)


class AdminRun(Shape):
    """A run without its message, its result or the reason it stopped."""

    run_id: UUID
    user_name: str
    roles: tuple[str, ...]
    region: str | None
    ticket_id: int | None
    status: str
    model: str
    tokens_in: int
    tokens_out: int
    tokens_cached: int
    estimated_cost_inr: Decimal | None
    tool_calls: int
    model_turns: int
    elapsed_ms: int
    started_at: datetime
    finished_at: datetime | None


class AdminRuns(Shape):
    items: list[AdminRun]


class AdminRunDetail(Shape):
    run: AdminRun
    events: list[dict[str, Any]]


def admin_run(run: Run) -> AdminRun:
    return AdminRun(
        run_id=run.id,
        user_name=run.person.name,
        roles=run.person.roles,
        region=run.person.region,
        ticket_id=run.ticket_id,
        status=run.status,
        model=run.model,
        tokens_in=run.tokens_in,
        tokens_out=run.tokens_out,
        tokens_cached=run.tokens_cached,
        estimated_cost_inr=run.estimated_cost_inr,
        tool_calls=run.tool_calls,
        model_turns=run.model_turns,
        elapsed_ms=run.elapsed_ms,
        started_at=run.created_at,
        finished_at=run.finished_at,
    )


@router.get("/runs")
async def runs(request: Request, _admin: Admin) -> AdminRuns:
    agent = agent_of(request)
    found = await agent.store.recent(agent.limits.runs.list_max)
    return AdminRuns(items=[admin_run(run) for run in found])


@router.get("/runs/latest/events", response_model=None)
async def live(request: Request, _admin: Admin) -> StreamingResponse:
    """The Live trace: follows whichever run started most recently."""
    agent = agent_of(request)
    stream = EventStream(agent.store, agent.bus, agent.limits.stream)
    return StreamingResponse(
        stream.of_latest_run(request.is_disconnected, admin_view),
        media_type=MEDIA_TYPE,
        headers=HEADERS,
    )


@router.get("/runs/{run_id}")
async def run_detail(request: Request, _admin: Admin, run_id: UUID) -> AdminRunDetail:
    agent = agent_of(request)
    run = await agent.store.get(run_id)
    if run is None:
        raise NotFoundError("No run with that id.")
    stored = await agent.store.events_after(run_id, 0, agent.limits.stream.page)
    return AdminRunDetail(run=admin_run(run), events=[admin_view(event) for event in stored])

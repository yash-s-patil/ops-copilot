"""Exists only where the demonstration switches exist. Never registered in production.

One tool call of a run exactly as the model received it, for the Technical details switch.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from opscp_agent.api.access import Answered, SignedIn, agent_of
from opscp_agent.api.runs import own_run
from opscp_common.errors import NotFoundError

router = APIRouter(prefix="/api/runs", route_class=Answered)


class CallDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: str
    tool: str
    args: dict[str, Any]
    result: str | None


@router.get("/{run_id}/calls/{call_id}")
async def call_detail(request: Request, person: SignedIn, run_id: UUID, call_id: str) -> CallDetail:
    run = await own_run(request, person, run_id)
    saved = await agent_of(request).manager.saved_state(run.id)
    messages: list[dict[str, Any]] = saved.get("messages", [])
    asked = next(
        (
            call
            for message in messages
            for call in message.get("tool_calls", [])
            if call["id"] == call_id
        ),
        None,
    )
    if asked is None:
        raise NotFoundError("No step with that id in this investigation.")
    result = next(
        (message["content"] for message in messages if message.get("tool_call_id") == call_id),
        None,
    )
    return CallDetail(call_id=call_id, tool=asked["name"], args=asked["args"], result=result)

"""Who the caller is. What their role allows is asked of the MCP server, which decides it."""

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from opscp_agent.api.access import Answered, SignedIn, agent_of

router = APIRouter(prefix="/api", route_class=Answered)


class Me(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    roles: tuple[str, ...]
    region: str | None
    permissions: tuple[str, ...]
    demo_tools: bool


@router.get("/me")
async def me(request: Request, caller: SignedIn) -> Me:
    agent = agent_of(request)
    async with agent.gateway.session(caller.token) as mcp:
        known = await mcp.identity()
    return Me(
        name=caller.person.name,
        roles=tuple(sorted(caller.person.roles)),
        region=caller.person.region,
        permissions=tuple(known.get("permissions", ())),
        demo_tools=agent.demo_tools,
    )

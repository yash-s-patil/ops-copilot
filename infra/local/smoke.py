"""Checks sign-in on the running local MCP server and agent, end to end over HTTP.

uv run --env-file .env python infra/local/smoke.py http://127.0.0.1:8000 http://127.0.0.1:8001
"""

import asyncio
import sys

import httpx2
from dev_token import AGENT, token_for
from fastmcp import Client

MCP_PATH = "/mcp"
TIMEOUT_S = 10
EXPECTED_TOOLS = {"priya": 7, "arjun": 8, "sam": 0}


async def _tools_listed(url: str, user: str) -> int:
    async with Client(url, auth=token_for(user), timeout=TIMEOUT_S) as client:
        return len(await client.list_tools())


async def _agent_refuses(http: httpx2.AsyncClient, agent_url: str) -> str | None:
    """Why the agent failed the check, or None. It must exchange tokens, never pass one on."""
    me = agent_url.rstrip("/") + "/api/me"
    unsigned = await http.get(me)
    if unsigned.status_code != httpx2.codes.UNAUTHORIZED:
        return f"The agent answered a request without a token with {unsigned.status_code}."
    for_mcp = await http.get(me, headers={"Authorization": f"Bearer {token_for('priya')}"})
    if for_mcp.status_code != httpx2.codes.UNAUTHORIZED:
        return f"The agent accepted a token meant for the MCP server ({for_mcp.status_code})."
    signed_in = await http.get(me, headers={"Authorization": f"Bearer {token_for('priya', AGENT)}"})
    if signed_in.status_code != httpx2.codes.OK:
        return f"The agent answered its own caller with {signed_in.status_code}."
    if "refunds.request" in signed_in.json()["permissions"]:
        return "The agent reports a permission an L1 engineer does not hold."
    return None


async def main(base_url: str, agent_url: str) -> int:
    url = base_url.rstrip("/") + MCP_PATH
    async with httpx2.AsyncClient(timeout=TIMEOUT_S) as http:
        unsigned = await http.post(url, json={})
        refusal = await _agent_refuses(http, agent_url)
    if unsigned.status_code != httpx2.codes.UNAUTHORIZED:
        sys.stderr.write(f"A request without a token was answered {unsigned.status_code}.\n")
        return 1
    if refusal is not None:
        sys.stderr.write(refusal + "\n")
        return 1
    for user, expected in EXPECTED_TOOLS.items():
        listed = await _tools_listed(url, user)
        if listed != expected:
            sys.stderr.write(f"{user} was shown {listed} tools, not {expected}.\n")
            return 1
    sys.stdout.write(
        "Sign-in works: no token is refused, each user sees their own tools, and the agent "
        "reaches the MCP server only with a token exchanged for it.\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1], sys.argv[2])))

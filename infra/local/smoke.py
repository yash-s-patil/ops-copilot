"""Checks sign-in on the running local MCP server, end to end over HTTP.

uv run --env-file .env python infra/local/smoke.py http://127.0.0.1:8000
"""

import asyncio
import sys

import httpx2
from dev_token import token_for
from fastmcp import Client

MCP_PATH = "/mcp"
TIMEOUT_S = 10
EXPECTED_TOOLS = {"priya": 6, "sam": 0}


async def _tools_listed(url: str, user: str) -> int:
    async with Client(url, auth=token_for(user), timeout=TIMEOUT_S) as client:
        return len(await client.list_tools())


async def main(base_url: str) -> int:
    url = base_url.rstrip("/") + MCP_PATH
    async with httpx2.AsyncClient(timeout=TIMEOUT_S) as http:
        unsigned = await http.post(url, json={})
    if unsigned.status_code != httpx2.codes.UNAUTHORIZED:
        sys.stderr.write(f"A request without a token was answered {unsigned.status_code}.\n")
        return 1
    for user, expected in EXPECTED_TOOLS.items():
        listed = await _tools_listed(url, user)
        if listed != expected:
            sys.stderr.write(f"{user} was shown {listed} tools, not {expected}.\n")
            return 1
    sys.stdout.write("Sign-in works: no token is refused, and each user sees their own tools.\n")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1])))

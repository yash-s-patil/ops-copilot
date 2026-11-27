"""Sign-in as part of the loop: a refused token is exchanged again, once, without losing work."""

import json

import httpx2

from opscp_agent.events import types as events
from opscp_agent.mcp_client.client import RUN_HEADER
from opscp_agent_testing.desk import bearer, desk
from opscp_agent_testing.reference import FIRST_HALF, FRESH_ORDER
from opscp_common.limits import Limits
from opscp_mcp_testing.identities import PRIYA

ASK = f"What is wrong with order {FRESH_ORDER}?"


def _calls(request: httpx2.Request, tool: str) -> bool:
    if not request.content:
        return False
    body = json.loads(request.content)
    return body.get("method") == "tools/call" and body["params"]["name"] == tool


class _Expire:
    """Answers the first `times` calls of one tool with 401, as for an expired token."""

    def __init__(self, tool: str, times: int) -> None:
        self._tool = tool
        self.left = times

    def __call__(self, request: httpx2.Request) -> httpx2.Response | None:
        if self.left and _calls(request, self._tool):
            self.left -= 1
            return httpx2.Response(401, headers={"WWW-Authenticate": "Bearer"})
        return None


async def test_a_refused_token_is_exchanged_again_and_the_call_repeated_once(
    limits: Limits,
) -> None:
    async with desk(limits, FIRST_HALF, mcp_handler=_Expire("get_order", times=1)) as at:
        run_id = await at.start(PRIYA, ASK)
        await at.settled(run_id, "completed")

        (refreshed,) = at.store.payloads(run_id, events.AUTH_REFRESHED)
        first = at.store.payloads(run_id, events.TOOL_FINISHED)[0]
        assert refreshed["call_id"] == first["call_id"]
        assert first["status"] == "ok"
        assert at.issuer.exchanges == 2
        reads = [entry.target for entry in at.audit.entries if entry.run_id == str(run_id)]
        assert reads.count("get_order") == 1


async def test_a_second_refusal_pauses_the_run_until_the_person_signs_in_again(
    limits: Limits,
) -> None:
    expire = _Expire("get_ticket", times=2)
    async with desk(limits, FIRST_HALF, mcp_handler=expire) as at:
        run_id = await at.start(PRIYA, ASK)
        await at.settled(run_id, "reauth_required")

        (paused,) = at.store.payloads(run_id, events.RUN_PAUSED)
        assert paused["code"] == "reauth_required"
        assert expire.left == 0
        before = [step["tool"] for step in at.store.payloads(run_id, events.TOOL_FINISHED)]
        assert "get_ticket" not in before

        # The person signs in again and the page reconnects, which picks the run up.
        seen = await at.agent.get(f"/api/runs/{run_id}", headers=bearer(PRIYA))
        assert seen.status_code == 200
        await at.settled(run_id, "completed")

        finished = [step["tool"] for step in at.store.payloads(run_id, events.TOOL_FINISHED)]
        assert finished.count("get_order") == 1
        assert "get_ticket" in finished


async def test_every_call_of_a_run_names_the_run_to_the_server(limits: Limits) -> None:
    async with desk(limits, FIRST_HALF) as at:
        run_id = await at.start(PRIYA, ASK)
        await at.settled(run_id, "completed")

        in_run = [request for request in at.mcp_requests if RUN_HEADER in request.headers]
        assert in_run
        assert {request.headers[RUN_HEADER] for request in in_run} == {str(run_id)}


async def test_when_the_mcp_server_cannot_be_reached_a_run_fails_cleanly(limits: Limits) -> None:
    def gone(request: httpx2.Request) -> httpx2.Response | None:
        if _calls(request, "get_order"):
            raise httpx2.ConnectError("refused", request=request)
        return None

    async with desk(limits, FIRST_HALF, mcp_handler=gone) as at:
        run_id = await at.start(PRIYA, ASK)
        await at.settled(run_id, "failed")

        (failed,) = at.store.payloads(run_id, events.RUN_FAILED)
        assert failed["code"] == "BACKEND_UNAVAILABLE"
        assert "refused" not in failed["message"]

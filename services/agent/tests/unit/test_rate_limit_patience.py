"""What a run does when the MCP server says its owner has asked too often."""

import json
from typing import Any
from uuid import UUID

import httpx2

from opscp_agent.events import types as events
from opscp_agent.mcp_client.client import Patience
from opscp_agent_testing.desk import Desk, bearer, desk
from opscp_agent_testing.reference import FIRST_HALF, SECOND_HALF, TICKET
from opscp_common.errors import BackendUnavailableError, RateLimitedError
from opscp_common.limits import Limits
from opscp_mcp.ports.controls import Allowance
from opscp_mcp_testing.identities import ARJUN, PRIYA

WAITS = 3
MAX_S = 5


class Refusing:
    """Refuses the requests whose turn is named, each with the same wait."""

    def __init__(self, turns: set[int], retry_after_s: int = 2) -> None:
        self.turns = turns
        self._retry_after_s = retry_after_s
        self.taken = 0

    async def take(self, user_oid: UUID) -> Allowance:
        self.taken += 1
        if self.taken in self.turns:
            return Allowance(allowed=False, retry_after_s=self._retry_after_s)
        return Allowance(allowed=True, retry_after_s=0)


class Slept:
    def __init__(self) -> None:
        self.seconds: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.seconds.append(seconds)


def _patience(slept: Slept) -> Patience:
    return Patience(waits=WAITS, max_s=MAX_S, sleep=slept)


def _refused_steps(at: Desk, run_id: UUID) -> list[str]:
    return [
        finished["tool"]
        for finished in at.store.payloads(run_id, events.TOOL_FINISHED)
        if finished["error_code"] == "RATE_LIMITED"
    ]


async def test_a_run_waits_the_time_the_server_names_and_asks_again(limits: Limits) -> None:
    slept = Slept()
    rates = Refusing({3, 4})
    async with desk(
        limits, FIRST_HALF, mcp_services={"rates": rates}, run_patience=_patience(slept)
    ) as at:
        run_id = await at.start(PRIYA, "Investigate.", TICKET)
        await at.settled(run_id, "awaiting_approval")

        assert slept.seconds == [2, 2]
        assert _refused_steps(at, run_id) == []


async def test_an_approval_refused_by_the_rate_limit_is_asked_for_again(limits: Limits) -> None:
    slept = Slept()
    rates = Refusing(set())
    async with desk(
        limits, SECOND_HALF, mcp_services={"rates": rates}, run_patience=_patience(slept)
    ) as at:
        run_id = await at.start(ARJUN, "Investigate.", TICKET)
        await at.settled(run_id, "awaiting_approval")
        # The approval and the request for its token, which follow the click.
        rates.turns = {rates.taken + 2, rates.taken + 3}

        await at.decide(ARJUN, run_id)

        assert await at.settled(run_id, "completed") == "completed"
        assert slept.seconds == [2, 2]


async def test_a_run_gives_up_after_the_waits_it_is_allowed(limits: Limits) -> None:
    slept = Slept()
    rates = Refusing(set(range(3, 100)))
    async with desk(
        limits, FIRST_HALF, mcp_services={"rates": rates}, run_patience=_patience(slept)
    ) as at:
        run_id = await at.start(PRIYA, "Investigate.", TICKET)
        await at.settled(run_id, "completed", "failed", "stopped", "awaiting_approval")

        assert slept.seconds[:WAITS] == [2] * WAITS
        assert _refused_steps(at, run_id) != []


async def test_a_wait_longer_than_the_longest_allowed_is_not_waited(limits: Limits) -> None:
    slept = Slept()
    rates = Refusing({3}, retry_after_s=MAX_S + 1)
    async with desk(
        limits, FIRST_HALF, mcp_services={"rates": rates}, run_patience=_patience(slept)
    ) as at:
        run_id = await at.start(PRIYA, "Investigate.", TICKET)
        await at.settled(run_id, "completed", "failed", "stopped", "awaiting_approval")

        assert slept.seconds == []


async def test_a_screen_is_answered_at_once_so_the_browser_can_wait_instead(
    limits: Limits,
) -> None:
    slept = Slept()
    rates = Refusing({1})
    async with desk(
        limits, FIRST_HALF, mcp_services={"rates": rates}, run_patience=_patience(slept)
    ) as at:
        response = await at.agent.get("/api/tickets?view=my_queue", headers=bearer(PRIYA))

        assert (response.status_code, response.headers["Retry-After"]) == (429, "2")
        assert slept.seconds == []


def test_only_a_rate_limit_with_a_wait_in_range_is_waited_for() -> None:
    patience = Patience(waits=1, max_s=MAX_S)

    assert patience.named_by(None) is None
    assert patience.named_by(BackendUnavailableError("Away.").body()) is None
    assert patience.named_by(RateLimitedError("Slow.", retry_after_seconds=MAX_S).body()) == MAX_S
    assert patience.named_by(RateLimitedError("Slow.", retry_after_seconds=-1).body()) is None
    not_a_number = (
        RateLimitedError("Slow.", retry_after_seconds=1)
        .body()
        .model_copy(update={"details": {"retry_after_seconds": True}})
    )
    assert patience.named_by(not_a_number) is None


async def test_a_sign_in_renewed_on_an_attempt_that_was_then_refused_is_still_reported(
    limits: Limits,
) -> None:
    slept = Slept()
    rates = Refusing(set())
    expired = {"left": 1}

    def expire_then_limit(request: httpx2.Request) -> httpx2.Response | None:
        body: dict[str, Any] = json.loads(request.content) if request.content else {}
        is_order = body.get("method") == "tools/call" and body["params"]["name"] == "get_order"
        if not (is_order and expired["left"]):
            return None
        expired["left"] = 0
        # The repeat after the token is exchanged again is the next request counted.
        rates.turns = {rates.taken + 1}
        return httpx2.Response(401, headers={"WWW-Authenticate": "Bearer"})

    async with desk(
        limits,
        FIRST_HALF,
        mcp_services={"rates": rates},
        mcp_handler=expire_then_limit,
        run_patience=_patience(slept),
    ) as at:
        run_id = await at.start(PRIYA, "Investigate.", TICKET)
        await at.settled(run_id, "awaiting_approval")

        (refreshed,) = at.store.payloads(run_id, events.AUTH_REFRESHED)
        first = at.store.payloads(run_id, events.TOOL_FINISHED)[0]
        assert (first["tool"], first["status"]) == ("get_order", "ok")
        assert refreshed["call_id"] == first["call_id"]
        assert slept.seconds == [2]

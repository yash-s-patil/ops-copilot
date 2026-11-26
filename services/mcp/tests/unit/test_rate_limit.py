"""How many requests one person may make, and what happens to the next one."""

import asyncio
import json
from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import UUID

import pytest
import structlog
from fastmcp.exceptions import McpError

from opscp_common.errors import ErrorCode
from opscp_common.limits import Limits
from opscp_mcp.authz.identity import Identity
from opscp_mcp.ports.controls import Allowance
from opscp_mcp_testing.fakes.controls import FakeAuditTrail, FakeRateLimiter
from opscp_mcp_testing.identities import ARJUN, PRIYA
from opscp_mcp_testing.server import call, connected, error_of, server_for
from opscp_mcp_testing.world import NOW, STUCK_ON_ADDRESS

PER_MINUTE = 60
BURST = 3


class _Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def _limiter(clock: Callable[[], datetime]) -> FakeRateLimiter:
    return FakeRateLimiter(per_minute=PER_MINUTE, burst=BURST, clock=clock)


class _Whoever:
    """Lets one server be called as different people in turn."""

    def __init__(self) -> None:
        self.identity = PRIYA

    def current(self) -> Identity:
        return self.identity


async def test_calls_beyond_the_burst_are_refused_with_the_time_to_wait(limits: Limits) -> None:
    clock = _Clock()

    async with connected(server_for(PRIYA, limits, rates=_limiter(clock))) as client:
        allowed = [await call(client, "get_order", order_id=STUCK_ON_ADDRESS) for _ in range(BURST)]
        refused = error_of(await call(client, "get_order", order_id=STUCK_ON_ADDRESS))

    assert [result.is_error for result in allowed] == [False] * BURST
    assert (refused.code, refused.retryable) == (ErrorCode.RATE_LIMITED, True)
    assert refused.details == {"retry_after_seconds": 1}


async def test_the_allowance_comes_back_at_the_stated_rate(limits: Limits) -> None:
    clock = _Clock()

    async with connected(server_for(PRIYA, limits, rates=_limiter(clock))) as client:
        for _ in range(BURST):
            await call(client, "get_order", order_id=STUCK_ON_ADDRESS)
        clock.advance(0.5)
        too_soon = await call(client, "get_order", order_id=STUCK_ON_ADDRESS)
        clock.advance(0.5)
        after_a_second = await call(client, "get_order", order_id=STUCK_ON_ADDRESS)
        straight_after = await call(client, "get_order", order_id=STUCK_ON_ADDRESS)
        clock.advance(3600)
        after_an_hour = [
            await call(client, "get_order", order_id=STUCK_ON_ADDRESS) for _ in range(BURST + 1)
        ]

    assert (too_soon.is_error, after_a_second.is_error, straight_after.is_error) == (
        True,
        False,
        True,
    )
    assert [result.is_error for result in after_an_hour] == [False] * BURST + [True]


async def test_one_persons_calls_do_not_use_up_anothers(limits: Limits) -> None:
    whoever = _Whoever()
    server = server_for(PRIYA, limits, rates=_limiter(_Clock()), identities=whoever)

    async with connected(server) as client:
        for _ in range(BURST):
            await call(client, "get_order", order_id=STUCK_ON_ADDRESS)
        priya = await call(client, "get_order", order_id=STUCK_ON_ADDRESS)
        whoever.identity = ARJUN
        arjun = await call(client, "get_order", order_id=STUCK_ON_ADDRESS)

    assert (priya.is_error, arjun.is_error) == (True, False)


async def test_resources_and_prompts_count_and_lists_do_not(limits: Limits) -> None:
    async with connected(server_for(PRIYA, limits, rates=_limiter(_Clock()))) as client:
        for _ in range(BURST * 2):
            await client.list_tools()
            await client.list_resources()
            await client.list_prompts()
        await client.read_resource("meridian://runbooks/index")
        await client.get_prompt("investigate_order", {"order_id": STUCK_ON_ADDRESS})
        await call(client, "get_order", order_id=STUCK_ON_ADDRESS)
        with pytest.raises(McpError, match="RATE_LIMITED"):
            await client.read_resource("meridian://runbooks/index")
        with pytest.raises(McpError, match="RATE_LIMITED"):
            await client.get_prompt("investigate_order", {"order_id": STUCK_ON_ADDRESS})


async def test_a_call_that_will_be_refused_anyway_still_counts(limits: Limits) -> None:
    async with connected(server_for(PRIYA, limits, rates=_limiter(_Clock()))) as client:
        codes = [error_of(await call(client, "no_such_tool")).code for _ in range(BURST + 1)]

    assert codes == [ErrorCode.NOT_FOUND] * BURST + [ErrorCode.RATE_LIMITED]


async def test_a_refused_request_is_not_run_and_leaves_no_audit_row(limits: Limits) -> None:
    audit = FakeAuditTrail()
    server = server_for(PRIYA, limits, rates=_limiter(_Clock()), audit=audit)

    async with connected(server) as client:
        for _ in range(BURST + 5):
            await call(client, "get_order", order_id=STUCK_ON_ADDRESS)

    assert len(audit.entries) == BURST


async def test_a_refusal_is_logged_with_who_and_never_with_what_they_typed(
    limits: Limits,
) -> None:
    made_up = "find asha.rao@example.in +91 90000 00001"
    limiter = FakeRateLimiter(per_minute=PER_MINUTE, burst=1, clock=lambda: NOW)

    async with connected(server_for(PRIYA, limits, rates=limiter)) as client:
        await call(client, "get_order", order_id=STUCK_ON_ADDRESS)
        with structlog.testing.capture_logs() as logs:
            await call(client, made_up)
            await call(client, "get_order", order_id=STUCK_ON_ADDRESS)
            with pytest.raises(McpError):
                await client.read_resource("meridian://customers/asha.rao@example.in")
            with pytest.raises(McpError):
                await client.get_prompt("mail asha.rao@example.in")

    refusals = [line for line in logs if line["event"] == "request_refused"]
    assert "asha" not in json.dumps(logs)
    assert "90000" not in json.dumps(logs)
    assert [line["during"] for line in refusals] == [
        "an unknown tool",
        "get_order",
        "an unknown resource",
        "an unknown prompt",
    ]
    assert [line for line in logs if line["event"] == "rate_limited"] == [
        {
            "event": "rate_limited",
            "log_level": "info",
            "actor_oid": str(PRIYA.oid),
            "retry_after_s": 1,
        }
    ] * 4


class _Stalled:
    async def take(self, _user_oid: UUID) -> Allowance:
        await asyncio.sleep(3600)
        raise AssertionError


class _Down:
    async def take(self, _user_oid: UUID) -> Allowance:
        raise ConnectionError("rate limit database down")


async def test_a_request_fails_when_it_cannot_be_counted(limits: Limits) -> None:
    quick = limits.model_copy(
        update={"timeout": limits.timeout.model_copy(update={"postgres_s": 0})}
    )

    async with connected(server_for(PRIYA, quick, rates=_Stalled())) as stalled:
        timed_out = error_of(await call(stalled, "get_order", order_id=STUCK_ON_ADDRESS))
    async with connected(server_for(PRIYA, limits, rates=_Down())) as down:
        failed = error_of(await call(down, "get_order", order_id=STUCK_ON_ADDRESS))

    assert (timed_out.code, failed.code) == (ErrorCode.TIMEOUT, ErrorCode.INTERNAL)


async def test_the_wait_is_never_reported_as_less_than_a_second() -> None:
    slow = FakeRateLimiter(per_minute=1, burst=1, clock=lambda: NOW)

    first, second = await slow.take(PRIYA.oid), await slow.take(PRIYA.oid)

    assert (first, second) == (
        Allowance(allowed=True, retry_after_s=0),
        Allowance(allowed=False, retry_after_s=60),
    )


async def test_one_persons_flood_is_counted_one_request_at_a_time(limits: Limits) -> None:
    class _Watched:
        def __init__(self) -> None:
            self.counting: dict[UUID, int] = {}
            self.most: dict[UUID, int] = {}

        async def take(self, user_oid: UUID) -> Allowance:
            self.counting[user_oid] = self.counting.get(user_oid, 0) + 1
            self.most[user_oid] = max(self.most.get(user_oid, 0), self.counting[user_oid])
            await asyncio.sleep(0.001)
            self.counting[user_oid] -= 1
            return Allowance(allowed=True, retry_after_s=0)

    rates, whoever = _Watched(), _Whoever()
    server = server_for(PRIYA, limits, rates=rates, identities=whoever)

    async with connected(server) as client:
        flood = [call(client, "get_order", order_id=STUCK_ON_ADDRESS) for _ in range(30)]
        await asyncio.gather(*flood)
        whoever.identity = ARJUN
        await asyncio.gather(
            *(call(client, "get_order", order_id=STUCK_ON_ADDRESS) for _ in range(5))
        )

    assert rates.most == {PRIYA.oid: 1, ARJUN.oid: 1}

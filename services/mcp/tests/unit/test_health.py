"""What the server reports about the systems it relies on, and how a failing one is handled."""

import json
from typing import Any

import pytest
from fastmcp.exceptions import McpError

from opscp_common.errors import BackendUnavailableError, ErrorCode
from opscp_common.limits import Limits
from opscp_mcp import container
from opscp_mcp.authz.identity import Identity
from opscp_mcp.authz.policy import Role
from opscp_mcp.ports.meridian import OrderRecord, Scope
from opscp_mcp.reliability.guard import Guard
from opscp_mcp.services.health import HEALTH_URI, Health, HealthService
from opscp_mcp.services.orders import OrderService
from opscp_mcp_testing.fakes.controls import FakeAuditTrail, FakeRateLimiter
from opscp_mcp_testing.fakes.meridian import FakeOrders
from opscp_mcp_testing.guards import FAILURES, HALF_OPEN_AFTER_S, SettableClock, a_guard
from opscp_mcp_testing.identities import ARJUN, KAVITA, PRIYA, ROHAN, SAM
from opscp_mcp_testing.server import (
    TestClient,
    call,
    connected,
    data_of,
    error_of,
    server_for,
    text_of,
)
from opscp_mcp_testing.world import NOW, STUCK_ON_ADDRESS, WORLD

RELIES_ON = {"postgres": ("get_order", "lookup_customer"), "blob": ("get_policy_document",)}


class _OrdersBehindAGuard:
    """The order store as the server reaches it: through a guard, and able to go down."""

    def __init__(self, guard: Guard) -> None:
        self._orders = FakeOrders(WORLD)
        self._guard = guard
        self.down = False
        self.reached = 0

    async def get(self, scope: Scope, order_id: int, *, recent_events: int) -> OrderRecord | None:
        async def read() -> OrderRecord | None:
            self.reached += 1
            if self.down:
                raise ConnectionError("connection refused by 10.0.0.4:5432")
            return await self._orders.get(scope, order_id, recent_events=recent_events)

        return await self._guard.run(read, retry=True)


async def _health(client: TestClient) -> dict[str, Any]:
    (content,) = await client.read_resource(HEALTH_URI)
    return json.loads(text_of(content))


def test_every_backend_is_reported_ok_while_its_breaker_is_closed() -> None:
    service = HealthService((a_guard("postgres"), a_guard("blob")), RELIES_ON)

    assert service.report(PRIYA) == Health(backends={"postgres": "ok", "blob": "ok"}, degraded=[])


@pytest.mark.parametrize("identity", [PRIYA, ROHAN, ARJUN, KAVITA, SAM], ids=lambda i: i.name)
async def test_anyone_with_a_role_may_read_health(limits: Limits, identity: Identity) -> None:
    async with connected(server_for(identity, limits)) as client:
        listed = [str(resource.uri) for resource in await client.list_resources()]
        health = await _health(client)

    assert HEALTH_URI in listed
    assert health == {"backends": {}, "degraded": []}


async def test_someone_with_no_role_may_not(limits: Limits) -> None:
    nobody = PRIYA.model_copy(update={"roles": frozenset[Role]()})

    async with connected(server_for(nobody, limits)) as client:
        listed = await client.list_resources()
        with pytest.raises(McpError, match="PERMISSION_DENIED"):
            await client.read_resource(HEALTH_URI)

    assert listed == []


async def test_a_failing_backend_opens_its_breaker_and_health_says_what_is_affected(
    limits: Limits,
) -> None:
    clock = SettableClock()
    guard = a_guard("postgres", clock=clock)
    store = _OrdersBehindAGuard(guard)
    server = server_for(
        PRIYA,
        limits,
        orders=OrderService(store, limits, lambda: NOW),
        health=HealthService((guard, a_guard("blob")), RELIES_ON),
    )

    async with connected(server) as client:
        before = await _health(client)
        store.down = True
        failures = [
            error_of(await call(client, "get_order", order_id=STUCK_ON_ADDRESS)) for _ in range(4)
        ]
        reached_while_failing = store.reached
        during = await _health(client)
        store.down = False
        still_refused = error_of(await call(client, "get_order", order_id=STUCK_ON_ADDRESS))
        reached_while_open = store.reached
        clock.now += HALF_OPEN_AFTER_S
        recovering = await _health(client)
        probe = data_of(await call(client, "get_order", order_id=STUCK_ON_ADDRESS))
        after = await _health(client)

    assert before == {"backends": {"postgres": "ok", "blob": "ok"}, "degraded": []}
    assert {(body.code, body.retryable) for body in failures} == {
        (ErrorCode.BACKEND_UNAVAILABLE, True)
    }
    assert "10.0.0.4" not in "".join(body.model_dump_json() for body in failures)
    assert reached_while_failing == FAILURES
    assert during == {
        "backends": {"postgres": "unavailable", "blob": "ok"},
        "degraded": ["get_order", "lookup_customer"],
    }
    assert still_refused.code is ErrorCode.BACKEND_UNAVAILABLE
    assert reached_while_open == reached_while_failing
    assert store.reached == reached_while_failing + 1
    assert recovering["backends"]["postgres"] == "recovering"
    assert recovering["degraded"] == ["get_order", "lookup_customer"]
    assert probe["order_id"] == STUCK_ON_ADDRESS
    assert after == before


async def test_health_stays_readable_when_requests_can_no_longer_be_counted(
    limits: Limits,
) -> None:
    exhausted = FakeRateLimiter(per_minute=1, burst=1, clock=lambda: NOW)

    async with connected(server_for(PRIYA, limits, rates=exhausted)) as client:
        await call(client, "get_order", order_id=STUCK_ON_ADDRESS)
        refused = error_of(await call(client, "get_order", order_id=STUCK_ON_ADDRESS))
        readings = [await _health(client) for _ in range(5)]

    assert refused.code is ErrorCode.RATE_LIMITED
    assert readings == [{"backends": {}, "degraded": []}] * 5


async def test_a_call_refused_by_the_open_breaker_of_another_store_is_audited_as_unavailable(
    limits: Limits,
) -> None:
    guard = a_guard("postgres")
    store = _OrdersBehindAGuard(guard)
    store.down = True
    audit = FakeAuditTrail()
    server = server_for(PRIYA, limits, audit=audit, orders=OrderService(store, limits, lambda: NOW))

    async with connected(server) as client:
        for _ in range(3):
            await call(client, "get_order", order_id=STUCK_ON_ADDRESS)

    assert store.reached == FAILURES
    assert [(entry.outcome, entry.error_code) for entry in audit.entries] == [
        ("error", "BACKEND_UNAVAILABLE")
    ] * 3


async def _refused() -> None:
    raise ConnectionError


async def test_a_tool_that_relies_on_two_failing_backends_is_listed_once() -> None:
    postgres, blob = a_guard("postgres"), a_guard("blob")
    for guard in (postgres, blob):
        for _ in range(FAILURES):
            with pytest.raises(BackendUnavailableError):
                await guard.run(_refused, retry=False)

    health = HealthService((postgres, blob), RELIES_ON | {"blob": ("get_order",)}).report(PRIYA)

    assert health.degraded == ["get_order", "lookup_customer"]


async def test_in_production_every_tool_is_said_to_rely_on_the_database(limits: Limits) -> None:
    tools = await server_for(PRIYA, limits).list_tools(run_middleware=False)

    assert set(container.RELIES_ON[container.POSTGRES]) == {tool.name for tool in tools}
    assert set(container.RELIES_ON[container.BLOB]) == {"get_policy_document"}

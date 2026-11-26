import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import datetime

import pytest
import structlog
from fastmcp.exceptions import McpError

from opscp_common.errors import BackendUnavailableError, ErrorCode
from opscp_common.limits import Limits
from opscp_mcp.ports.meridian import OrderRecord, Scope
from opscp_mcp.resources.reference import VERSION_KEY
from opscp_mcp.services.health import HEALTH_URI
from opscp_mcp.services.orders import OrderService
from opscp_mcp.services.resources import (
    ORDER_LIFECYCLE_URI,
    REFUND_SUMMARY_URI,
    RUNBOOK_INDEX_URI,
)
from opscp_mcp_testing.identities import ARJUN, PRIYA, SAM
from opscp_mcp_testing.server import (
    TestClient,
    call,
    call_with,
    connected,
    data_of,
    error_of,
    server_for,
    text_of,
)
from opscp_mcp_testing.world import (
    ASHA,
    NO_ORDER,
    NOW,
    PLAIN,
    SCANNER_LOOP,
    SOUTH_ORDER,
    STUCK_ON_ADDRESS,
)

TOOLS = [
    "lookup_customer",
    "get_order",
    "list_tickets",
    "get_ticket",
    "search_runbooks",
    "get_policy_document",
]
RESOURCES = [ORDER_LIFECYCLE_URI, REFUND_SUMMARY_URI, RUNBOOK_INDEX_URI]
HOST = "db-opscp-demo.postgres.database.azure.com"


@pytest.fixture
async def priya(limits: Limits) -> AsyncGenerator[TestClient]:
    async with connected(server_for(PRIYA, limits)) as client:
        yield client


@pytest.fixture
async def sam(limits: Limits) -> AsyncGenerator[TestClient]:
    async with connected(server_for(SAM, limits)) as client:
        yield client


async def test_the_six_read_tools_are_published_read_only_with_both_schemas(
    priya: TestClient,
) -> None:
    tools = await priya.list_tools()

    assert [tool.name for tool in tools] == TOOLS
    for tool in tools:
        assert tool.annotations is not None
        assert (tool.annotations.read_only_hint, tool.annotations.idempotent_hint) == (True, True)
        assert tool.input_schema["additionalProperties"] is False
        assert tool.output_schema is not None
        assert set(tool.output_schema["required"]) == {"data", "provenance"}
        assert tool.description


async def test_the_reference_read_sequence_runs_end_to_end(priya: TestClient) -> None:
    order = data_of(await call(priya, "get_order", order_id=STUCK_ON_ADDRESS))
    customer = data_of(await call(priya, "lookup_customer", customer_id=order["customer_id"]))
    listed = data_of(await call(priya, "list_tickets", order_id=STUCK_ON_ADDRESS))
    ticket = data_of(await call(priya, "get_ticket", ticket_id=PLAIN))
    runbooks = data_of(
        await call(
            priya, "search_runbooks", query="address validation failed, order stuck in packed"
        )
    )
    policy = data_of(
        await call(priya, "get_policy_document", name="refund-policy", section="goodwill")
    )

    assert (order["status"], order["status_age_hours"], order["is_stuck"]) == ("packed", 96, True)
    assert order["events"]["recent"][0]["type"] == "address_validation_failed"
    assert (customer["customer_id"], customer["tier"], customer["contact_masked"]) == (
        ASHA,
        "premium",
        True,
    )
    assert [item["ticket_id"] for item in listed["items"]] == [PLAIN]
    assert ticket["summary"] == "Order stuck for four days"
    assert [hit["runbook_id"] for hit in runbooks["results"]][:2] == ["rb-001", "rb-008"]
    assert "| Premium | 72 hours | 15% of order value |" in policy["section"]["content"]


async def test_an_engineer_with_pii_access_reads_contact_details_through_the_tool(
    limits: Limits,
) -> None:
    async with connected(server_for(ARJUN, limits)) as arjun:
        customer = data_of(await call(arjun, "lookup_customer", email="asha.rao@example.in"))

    assert (customer["email"], customer["contact_masked"]) == ("asha.rao@example.in", False)


async def test_every_result_is_stamped_with_where_it_came_from(priya: TestClient) -> None:
    first = await call(priya, "get_order", order_id=STUCK_ON_ADDRESS, events="none")
    second = await call(priya, "get_ticket", ticket_id=PLAIN, notes=0)

    assert first.structured_content is not None
    assert second.structured_content is not None
    assert first.structured_content["provenance"] == {
        "id": "get_order-0001",
        "tool": "get_order",
        "retrieved_at": NOW.isoformat().replace("+00:00", "Z"),
    }
    assert second.structured_content["provenance"]["id"] == "get_ticket-0002"


async def test_the_text_of_a_result_is_the_same_json_as_its_structure(priya: TestClient) -> None:
    result = await call(priya, "get_order", order_id=STUCK_ON_ADDRESS, events="none")

    (block,) = result.content
    assert json.loads(text_of(block)) == result.structured_content


async def test_a_record_that_is_missing_or_elsewhere_is_reported_the_same_way(
    priya: TestClient,
) -> None:
    missing = error_of(await call(priya, "get_order", order_id=NO_ORDER))
    elsewhere = error_of(await call(priya, "get_order", order_id=SOUTH_ORDER))

    assert missing == elsewhere
    assert missing.model_dump(mode="json") == {
        "code": "NOT_FOUND",
        "message": "No order with that number is visible to you.",
        "retryable": False,
        "details": {},
    }


@pytest.mark.parametrize(
    ("tool", "arguments", "fields"),
    [
        pytest.param("get_order", {"order_id": "4471"}, ["order_id"], id="number as text"),
        pytest.param("get_order", {}, ["order_id"], id="missing argument"),
        pytest.param("get_order", {"order_id": 1, "role": "admin"}, ["role"], id="extra argument"),
        pytest.param(
            "get_order", {"order_id": 1, "events": "all"}, ["events"], id="unknown choice"
        ),
        pytest.param("get_policy_document", {"name": "pricing"}, ["name"], id="unknown document"),
        pytest.param("list_tickets", {"view": "everything"}, ["view"], id="unknown view"),
        pytest.param("get_ticket", {"ticket_id": 1.5}, ["ticket_id"], id="fraction as id"),
    ],
)
async def test_arguments_outside_the_schema_are_refused_without_being_quoted(
    priya: TestClient, tool: str, arguments: dict[str, object], fields: list[str]
) -> None:
    result = await call_with(priya, tool, arguments)

    body = error_of(result)
    assert body.model_dump(mode="json") == {
        "code": "VALIDATION_FAILED",
        "message": "The arguments do not match what this accepts.",
        "retryable": False,
        "details": {"fields": fields},
    }


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("lookup_customer", {}),
        ("lookup_customer", {"customer_id": ASHA, "email": "asha.rao@example.in"}),
        ("list_tickets", {}),
        ("list_tickets", {"view": "all_open", "page_size": 26}),
        ("get_ticket", {"ticket_id": PLAIN, "notes": 11}),
        ("search_runbooks", {"query": "ab"}),
        ("search_runbooks", {"query": "refund", "top_k": 6}),
        ("get_order", {"order_id": 2**63}),
    ],
)
async def test_values_outside_the_limits_are_refused_as_invalid(
    priya: TestClient, tool: str, arguments: dict[str, object]
) -> None:
    result = await call_with(priya, tool, arguments)

    assert error_of(result).code is ErrorCode.VALIDATION_FAILED


@pytest.mark.parametrize(
    ("tool", "arguments", "permission"),
    [
        ("lookup_customer", {"customer_id": ASHA}, "customers.read"),
        ("get_order", {"order_id": STUCK_ON_ADDRESS}, "orders.read"),
        ("list_tickets", {"view": "all_open"}, "tickets.read"),
        ("get_ticket", {"ticket_id": PLAIN}, "tickets.read"),
        ("search_runbooks", {"query": "refund"}, "runbooks.read"),
        ("get_policy_document", {"name": "refund-policy"}, "policies.read"),
    ],
)
async def test_an_administrator_is_refused_by_every_tool(
    sam: TestClient, tool: str, arguments: dict[str, object], permission: str
) -> None:
    body = error_of(await call_with(sam, tool, arguments))

    assert (body.code, body.details) == (ErrorCode.PERMISSION_DENIED, {"permission": permission})


async def test_a_tool_that_does_not_exist_is_reported_in_the_same_body(priya: TestClient) -> None:
    body = error_of(await call(priya, "issue_refund", order_id=STUCK_ON_ADDRESS))

    assert (body.code, body.message) == (
        ErrorCode.NOT_FOUND,
        "There is nothing with that name here.",
    )


class _Broken:
    def __init__(self, failure: Exception) -> None:
        self._failure = failure

    async def get(self, scope: Scope, order_id: int, *, recent_events: int) -> OrderRecord:
        raise self._failure


class _Slow:
    async def get(self, scope: Scope, order_id: int, *, recent_events: int) -> OrderRecord:
        await asyncio.sleep(60)
        raise AssertionError("the tool's time limit should have ended this call")


def _orders(limits: Limits, backend: object) -> OrderService:
    return OrderService(backend, limits, lambda: NOW)  # pyright: ignore[reportArgumentType] -- a failing stand-in


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError(f"connection to {HOST} refused while reading core.orders"),
        KeyError("status"),
        ConnectionError(f"could not connect to {HOST}:5432"),
    ],
    ids=lambda failure: type(failure).__name__,
)
async def test_an_unexpected_failure_says_nothing_about_its_cause(
    limits: Limits, failure: Exception
) -> None:
    server = server_for(PRIYA, limits, orders=_orders(limits, _Broken(failure)))

    with structlog.testing.capture_logs() as logs:
        async with connected(server) as client:
            result = await call(client, "get_order", order_id=STUCK_ON_ADDRESS)

    assert error_of(result).model_dump(mode="json") == {
        "code": "INTERNAL",
        "message": "Something went wrong on the server.",
        "retryable": False,
        "details": {},
    }
    (line,) = [entry for entry in logs if entry["event"] == "request_failed"]
    assert (line["during"], line["error"]) == ("get_order", type(failure).__name__)
    assert HOST not in json.dumps(logs)


async def test_a_backend_outage_is_reported_as_retryable(limits: Limits) -> None:
    outage = BackendUnavailableError("The database is unavailable.")
    server = server_for(PRIYA, limits, orders=_orders(limits, _Broken(outage)))

    async with connected(server) as client:
        body = error_of(await call(client, "get_order", order_id=STUCK_ON_ADDRESS))

    assert (body.code, body.retryable) == (ErrorCode.BACKEND_UNAVAILABLE, True)


async def test_a_tool_that_takes_too_long_is_stopped_and_reported_as_a_timeout(
    limits: Limits,
) -> None:
    quick = limits.model_copy(
        update={"tool_timeout": limits.tool_timeout.model_copy(update={"get_order_s": 1})}
    )
    server = server_for(PRIYA, quick, orders=_orders(quick, _Slow()))

    async with connected(server) as client:
        started = asyncio.get_running_loop().time()
        body = error_of(await call(client, "get_order", order_id=STUCK_ON_ADDRESS))
        elapsed = asyncio.get_running_loop().time() - started

    assert (body.code, body.retryable) == (ErrorCode.TIMEOUT, True)
    assert elapsed < 5


async def test_a_result_over_the_size_limit_is_never_sent(limits: Limits) -> None:
    tiny = limits.model_copy(
        update={"tool": limits.tool.model_copy(update={"result_bytes_max": 600})}
    )

    with structlog.testing.capture_logs() as logs:
        async with connected(server_for(PRIYA, tiny)) as client:
            large = await call(client, "get_order", order_id=SCANNER_LOOP)
            small = await call(client, "get_order", order_id=SCANNER_LOOP, events="none")

    assert error_of(large).model_dump(mode="json") == {
        "code": "INTERNAL",
        "message": "Something went wrong on the server.",
        "retryable": False,
        "details": {},
    }
    assert data_of(small)["order_id"] == SCANNER_LOOP
    (too_large,) = [entry for entry in logs if entry["event"] == "result_too_large"]
    assert too_large["during"] == "get_order"
    assert too_large["size"] > tiny.tool.result_bytes_max


async def test_real_results_stay_well_inside_the_size_limit(
    priya: TestClient, limits: Limits
) -> None:
    calls = [
        await call(priya, "get_order", order_id=SCANNER_LOOP),
        await call(priya, "list_tickets", view="all_open", page_size=limits.tickets.page_size_max),
        await call(priya, "search_runbooks", query="refund", top_k=limits.runbooks.top_k_max),
    ]

    for result in calls:
        size = len(json.dumps(result.structured_content, separators=(",", ":")).encode())
        assert 0 < size < limits.tool.result_bytes_max


async def test_the_reference_resources_are_listed_as_plain_text_and_health_as_json(
    priya: TestClient,
) -> None:
    resources = await priya.list_resources()

    assert [(str(resource.uri), resource.mime_type) for resource in resources] == [
        *((uri, "text/plain") for uri in RESOURCES),
        (HEALTH_URI, "application/json"),
    ]
    assert all(resource.description for resource in resources)


@pytest.mark.parametrize("uri", RESOURCES)
async def test_a_resource_is_read_with_its_version(priya: TestClient, uri: str) -> None:
    read = await priya.read_resource_mcp(uri)

    (content,) = read.contents
    assert read.meta is not None
    version = read.meta[VERSION_KEY]
    assert len(version) == 12
    assert content.meta == {VERSION_KEY: version}
    assert text_of(content)


async def test_a_resource_keeps_its_version_until_its_text_changes(
    priya: TestClient, limits: Limits
) -> None:
    changed = limits.model_copy(
        update={"stuck": limits.stuck.model_copy(update={"packed_hours": 49})}
    )

    first = await priya.read_resource_mcp(ORDER_LIFECYCLE_URI)
    again = await priya.read_resource_mcp(ORDER_LIFECYCLE_URI)
    async with connected(server_for(PRIYA, changed)) as other:
        after = await other.read_resource_mcp(ORDER_LIFECYCLE_URI)

    assert first.meta is not None
    assert again.meta is not None
    assert after.meta is not None
    assert first.meta[VERSION_KEY] == again.meta[VERSION_KEY] != after.meta[VERSION_KEY]


async def test_an_administrator_cannot_read_a_resource(sam: TestClient) -> None:
    for uri in RESOURCES:
        with pytest.raises(McpError) as refusal:
            await sam.read_resource(uri)

        assert json.loads(str(refusal.value))["code"] == "PERMISSION_DENIED"


async def test_a_resource_that_does_not_exist_is_a_protocol_error(priya: TestClient) -> None:
    with pytest.raises(McpError, match="not found"):
        await priya.read_resource("meridian://reference/nothing")


async def test_the_two_prompts_are_listed_with_their_arguments(priya: TestClient) -> None:
    prompts = await priya.list_prompts()

    listed = {
        prompt.name: [(argument.name, argument.required) for argument in prompt.arguments or []]
        for prompt in prompts
    }
    assert listed == {
        "investigate_order": [("order_id", True), ("ticket_id", False)],
        "draft_customer_reply": [("ticket_id", True), ("tone", True)],
    }


async def test_a_prompt_is_returned_as_one_user_message(priya: TestClient) -> None:
    investigate = await priya.get_prompt("investigate_order", {"order_id": 4471})
    reply = await priya.get_prompt("draft_customer_reply", {"ticket_id": 2207, "tone": "firm"})

    (message,) = investigate.messages
    assert message.role == "user"
    assert text_of(message.content).startswith("Investigate order 4471")
    assert "Tone: firm." in text_of(reply.messages[0].content)


@pytest.mark.parametrize(
    "arguments",
    [
        {"ticket_id": 2207, "tone": "angry"},
        {"ticket_id": "abc", "tone": "firm"},
        {"tone": "firm"},
        {"ticket_id": 0, "tone": "firm"},
    ],
)
async def test_a_prompt_refuses_arguments_it_does_not_accept(
    priya: TestClient, arguments: dict[str, object]
) -> None:
    with pytest.raises(McpError) as refusal:
        await priya.get_prompt("draft_customer_reply", arguments)

    assert json.loads(str(refusal.value))["code"] == "VALIDATION_FAILED"


def test_the_clock_and_result_ids_of_a_real_server_are_not_fixed() -> None:
    from opscp_mcp.container import new_result_id, utc_now

    assert isinstance(utc_now(), datetime)
    assert utc_now().tzinfo is not None
    assert len({new_result_id() for _ in range(50)}) == 50

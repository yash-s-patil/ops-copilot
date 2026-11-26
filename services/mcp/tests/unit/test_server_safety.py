"""What the server must never do, whoever is asking and whatever they send."""

import contextlib
import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import structlog
from fastmcp.client.client import CallToolResult
from fastmcp.exceptions import McpError

from opscp_common.errors import ErrorCode
from opscp_common.limits import Limits
from opscp_mcp.authz.identity import Identity
from opscp_mcp.ports.meridian import OrderRecord, Scope
from opscp_mcp.services.health import HEALTH_URI
from opscp_mcp.services.orders import OrderService
from opscp_mcp.services.resources import (
    ORDER_LIFECYCLE_URI,
    REFUND_SUMMARY_URI,
    RUNBOOK_INDEX_URI,
)
from opscp_mcp_testing.identities import ARJUN, KAVITA, PRIYA, ROHAN, SAM
from opscp_mcp_testing.server import (
    SignedOut,
    TestClient,
    call,
    call_with,
    connected,
    error_of,
    server_for,
    text_of,
)
from opscp_mcp_testing.world import ASHA, NOW, PLAIN, STUCK_ON_ADDRESS

VALID_CALLS: dict[str, dict[str, Any]] = {
    "lookup_customer": {"customer_id": ASHA},
    "get_order": {"order_id": STUCK_ON_ADDRESS},
    "list_tickets": {"view": "all_open"},
    "get_ticket": {"ticket_id": PLAIN},
    "search_runbooks": {"query": "carrier exception"},
    "get_policy_document": {"name": "refund-policy"},
}
VALID_PROMPTS: dict[str, dict[str, Any]] = {
    "investigate_order": {"order_id": 4471},
    "draft_customer_reply": {"ticket_id": 2207, "tone": "firm"},
}
RESOURCES = [ORDER_LIFECYCLE_URI, REFUND_SUMMARY_URI, RUNBOOK_INDEX_URI, HEALTH_URI]
CUSTOMER_EMAIL = "asha.rao@example.in"


@pytest.fixture
async def priya(limits: Limits) -> AsyncGenerator[TestClient]:
    async with connected(server_for(PRIYA, limits)) as client:
        yield client


@pytest.fixture
async def nobody(limits: Limits) -> AsyncGenerator[TestClient]:
    async with connected(server_for(PRIYA, limits, identities=SignedOut())) as client:
        yield client


async def test_the_valid_calls_cover_every_tool_and_prompt(priya: TestClient) -> None:
    assert {tool.name for tool in await priya.list_tools()} == set(VALID_CALLS)
    assert {prompt.name for prompt in await priya.list_prompts()} == set(VALID_PROMPTS)
    for tool, arguments in VALID_CALLS.items():
        assert not (await call_with(priya, tool, arguments)).is_error


def _is_denial(result: CallToolResult) -> bool:
    return result.is_error and error_of(result).code is ErrorCode.PERMISSION_DENIED


@pytest.mark.parametrize("identity", [PRIYA, ROHAN, ARJUN, KAVITA, SAM], ids=lambda i: i.name)
async def test_a_caller_is_shown_exactly_what_they_would_not_be_refused(
    limits: Limits, identity: Identity
) -> None:
    """The list and the services are written separately, so they are checked against each other."""

    async def permitted(client: TestClient) -> set[str]:
        allowed = {
            tool
            for tool, arguments in VALID_CALLS.items()
            if not _is_denial(await call_with(client, tool, arguments))
        }
        for uri in RESOURCES:
            with contextlib.suppress(McpError):
                await client.read_resource(uri)
                allowed.add(uri)
        for prompt, arguments in VALID_PROMPTS.items():
            with contextlib.suppress(McpError):
                await client.get_prompt(prompt, arguments)
                allowed.add(prompt)
        return allowed

    async with connected(server_for(identity, limits)) as client:
        shown = (
            {tool.name for tool in await client.list_tools()}
            | {str(resource.uri) for resource in await client.list_resources()}
            | {prompt.name for prompt in await client.list_prompts()}
        )

        assert shown == await permitted(client)


@pytest.mark.parametrize("tool", VALID_CALLS)
async def test_no_tool_answers_a_well_formed_call_without_sign_in(
    nobody: TestClient, tool: str
) -> None:
    body = error_of(await call_with(nobody, tool, VALID_CALLS[tool]))

    assert body.code is ErrorCode.PERMISSION_DENIED
    assert body.details == {}


@pytest.mark.parametrize("uri", RESOURCES)
async def test_no_resource_is_read_without_sign_in(nobody: TestClient, uri: str) -> None:
    with pytest.raises(McpError) as refusal:
        await nobody.read_resource(uri)

    assert json.loads(str(refusal.value))["code"] == "PERMISSION_DENIED"


@pytest.mark.parametrize("prompt", VALID_PROMPTS)
async def test_no_prompt_is_given_without_sign_in(nobody: TestClient, prompt: str) -> None:
    with pytest.raises(McpError) as refusal:
        await nobody.get_prompt(prompt, VALID_PROMPTS[prompt])

    assert json.loads(str(refusal.value))["code"] == "PERMISSION_DENIED"


@pytest.mark.parametrize(
    ("prompt", "permission"),
    [("investigate_order", "orders.read"), ("draft_customer_reply", "tickets.read")],
)
async def test_an_administrator_is_refused_the_prompts(
    limits: Limits, prompt: str, permission: str
) -> None:
    async with connected(server_for(SAM, limits)) as sam:
        with pytest.raises(McpError) as refusal:
            await sam.get_prompt(prompt, VALID_PROMPTS[prompt])

    body = json.loads(str(refusal.value))
    assert (body["code"], body["details"]) == ("PERMISSION_DENIED", {"permission": permission})


async def test_a_tool_name_the_caller_made_up_is_not_written_to_the_log(
    priya: TestClient,
) -> None:
    made_up = f"no_such_tool {CUSTOMER_EMAIL}"

    with structlog.testing.capture_logs() as logs:
        body = error_of(await call_with(priya, made_up, {}))

    assert body.code is ErrorCode.NOT_FOUND
    assert CUSTOMER_EMAIL not in body.model_dump_json()
    audit, refused = logs
    assert (audit["event"], audit["target"], audit["args_redacted"]) == (
        "audit",
        "an unknown tool",
        {},
    )
    assert refused == {
        "event": "request_refused",
        "log_level": "info",
        "during": "an unknown tool",
        "code": "NOT_FOUND",
    }
    assert CUSTOMER_EMAIL not in json.dumps(logs)


async def test_argument_values_are_not_written_to_the_log(priya: TestClient) -> None:
    with structlog.testing.capture_logs() as logs:
        await call(priya, "lookup_customer", email=CUSTOMER_EMAIL)
        await call(priya, "lookup_customer", email="nobody@example.in")
        await call_with(priya, "get_order", {"order_id": CUSTOMER_EMAIL})

    assert CUSTOMER_EMAIL not in json.dumps(logs)
    assert "nobody@example.in" not in json.dumps(logs)


@pytest.mark.parametrize(
    "name",
    [CUSTOMER_EMAIL, "x" * 50_000, "role; DROP TABLE", "new\nline", "<script>", "é" * 10],
    ids=["email", "very long", "punctuation", "line break", "markup", "accents"],
)
async def test_an_argument_name_the_caller_made_up_is_not_handed_back(
    priya: TestClient, name: str
) -> None:
    result = await call_with(priya, "get_order", {"order_id": STUCK_ON_ADDRESS, name: "x"})

    body = error_of(result)
    assert body.code is ErrorCode.VALIDATION_FAILED
    assert name not in json.dumps(body.details, ensure_ascii=False)
    assert len(body.model_dump_json()) < 400


async def test_many_wrong_arguments_are_reported_up_to_a_limit(priya: TestClient) -> None:
    wrong = {f"extra_{number:03d}": number for number in range(200)}

    body = error_of(await call_with(priya, "get_order", {"order_id": STUCK_ON_ADDRESS} | wrong))

    fields = body.details["fields"]
    assert isinstance(fields, list)
    assert fields == [f"extra_{number:03d}" for number in range(16)]


class _StoredBadly:
    """Returns a row the service cannot turn into a result: a defect, not a bad argument."""

    async def get(self, scope: Scope, order_id: int, *, recent_events: int) -> OrderRecord:
        return OrderRecord.model_validate({"id": CUSTOMER_EMAIL})


async def test_a_validation_failure_inside_the_server_is_a_defect_not_a_bad_argument(
    limits: Limits,
) -> None:
    orders = OrderService(_StoredBadly(), limits, lambda: NOW)

    with structlog.testing.capture_logs() as logs:
        async with connected(server_for(PRIYA, limits, orders=orders)) as client:
            body = error_of(await call(client, "get_order", order_id=STUCK_ON_ADDRESS))

    assert (body.code, body.details) == (ErrorCode.INTERNAL, {})
    (failure,) = [entry for entry in logs if entry["event"] == "request_failed"]
    assert failure["error"] == "ValidationError"
    assert any(frame.endswith("in get") for frame in failure["where"])
    assert CUSTOMER_EMAIL not in json.dumps(logs)


async def test_a_prompt_does_not_quote_the_argument_it_refused(priya: TestClient) -> None:
    with pytest.raises(McpError) as refusal:
        await priya.get_prompt(
            "draft_customer_reply", {"ticket_id": 2207, "tone": f"angry {CUSTOMER_EMAIL}"}
        )

    assert CUSTOMER_EMAIL not in str(refusal.value)
    assert json.loads(str(refusal.value)) == {
        "code": "VALIDATION_FAILED",
        "message": "The arguments do not match what this accepts.",
        "retryable": False,
        "details": {"fields": []},
    }


async def test_the_size_limit_counts_the_text_a_model_reads(limits: Limits) -> None:
    async with connected(server_for(PRIYA, limits)) as client:
        result = await call(client, "get_order", order_id=STUCK_ON_ADDRESS)
    text_bytes = len(text_of(result.content[0]).encode())
    just_under = limits.model_copy(
        update={"tool": limits.tool.model_copy(update={"result_bytes_max": text_bytes - 1})}
    )

    async with connected(server_for(PRIYA, just_under)) as client:
        refused = await call(client, "get_order", order_id=STUCK_ON_ADDRESS)

    assert error_of(refused).code is ErrorCode.INTERNAL

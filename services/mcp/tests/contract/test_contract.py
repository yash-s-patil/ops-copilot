"""The published interface, held as snapshots.

A change here is a change to what every connected agent sees. To accept one on purpose, run
`UPDATE_CONTRACT_SNAPSHOTS=1 uv run pytest services/mcp/tests/contract`, review the diff of
the snapshot files, and add an entry to services/mcp/CHANGELOG.md.
"""

import json
import os
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest

from opscp_common.errors import (
    ApprovalInvalidError,
    BackendTimeoutError,
    BackendUnavailableError,
    ErrorCode,
    IdempotencyConflictError,
    InternalError,
    NotFoundError,
    OpsError,
    PermissionDeniedError,
    PolicyViolationError,
    RateLimitedError,
    ValidationFailedError,
)
from opscp_common.limits import Limits
from opscp_mcp.server import INSTRUCTIONS, SERVER_NAME
from opscp_mcp_testing.identities import KAVITA
from opscp_mcp_testing.server import TestClient, connected, server_for

SNAPSHOTS = Path(__file__).parent / "snapshots"
UPDATE = "UPDATE_CONTRACT_SNAPSHOTS"
ERRORS: list[OpsError] = [
    PermissionDeniedError("Your role does not allow this.", permission="orders.read"),
    ValidationFailedError("The arguments do not match what this accepts.", fields=["order_id"]),
    NotFoundError("No order with that number is visible to you."),
    PolicyViolationError(
        "Goodwill refund exceeds the 15% cap.", cap_minor=93000, requested_minor=620000
    ),
    ApprovalInvalidError("The approval is no longer valid."),
    IdempotencyConflictError("That key was used with different arguments."),
    RateLimitedError("Too many calls.", retry_after_seconds=7),
    BackendUnavailableError("The policy store is unavailable."),
    BackendTimeoutError("The request took too long. Try again."),
    InternalError(),
]


def _matches(name: str, actual: object) -> None:
    path = SNAPSHOTS / f"{name}.json"
    rendered = json.dumps(actual, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if os.environ.get(UPDATE):
        path.write_text(rendered, encoding="utf-8")
    assert path.exists(), f"no snapshot {path.name}; see the note at the top of this file"
    assert rendered == path.read_text(encoding="utf-8"), (
        f"{path.name} no longer matches the server; see the note at the top of this file"
    )


def _plain(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json", exclude_none=True, by_alias=True)


@pytest.fixture
async def client(limits: Limits) -> AsyncGenerator[TestClient]:
    async with connected(server_for(KAVITA, limits)) as connection:
        yield connection


async def test_the_tools_match_their_snapshot(client: TestClient) -> None:
    tools = [
        {
            "name": tool.name,
            "title": tool.title,
            "description": tool.description,
            "annotations": _plain(tool.annotations),
            "input_schema": tool.input_schema,
            "output_schema": tool.output_schema,
        }
        for tool in await client.list_tools()
    ]

    _matches("tools", tools)


async def test_the_resources_match_their_snapshot(client: TestClient) -> None:
    resources = [
        {
            "uri": str(resource.uri),
            "name": resource.name,
            "title": resource.title,
            "description": resource.description,
            "mime_type": resource.mime_type,
        }
        for resource in await client.list_resources()
    ]

    _matches("resources", resources)
    assert await client.list_resource_templates() == []


async def test_the_prompts_match_their_snapshot(client: TestClient) -> None:
    prompts = [
        {
            "name": prompt.name,
            "title": prompt.title,
            "description": prompt.description,
            "arguments": [_plain(argument) for argument in prompt.arguments or []],
        }
        for prompt in await client.list_prompts()
    ]

    _matches("prompts", prompts)


async def test_the_prompt_texts_match_their_snapshot(client: TestClient) -> None:
    calls: dict[str, dict[str, Any]] = {
        "investigate_order": {"order_id": 4471},
        "investigate_order_with_ticket": {"order_id": 4471, "ticket_id": 2207},
        "draft_customer_reply_neutral": {"ticket_id": 2207, "tone": "neutral"},
        "draft_customer_reply_apologetic": {"ticket_id": 2207, "tone": "apologetic"},
        "draft_customer_reply_firm": {"ticket_id": 2207, "tone": "firm"},
    }
    texts: dict[str, list[dict[str, Any]]] = {}
    for label, arguments in calls.items():
        name = "investigate_order" if label.startswith("investigate") else "draft_customer_reply"
        result = await client.get_prompt(name, arguments)
        texts[label] = [_plain(message) for message in result.messages]

    _matches("prompt_texts", texts)


def test_the_error_bodies_match_their_snapshot() -> None:
    bodies = {error.code.value: error.body().model_dump(mode="json") for error in ERRORS}

    assert list(bodies) == [code.value for code in ErrorCode]
    _matches("errors", bodies)


def test_the_server_introduction_matches_its_snapshot() -> None:
    _matches("server", {"name": SERVER_NAME, "instructions": INSTRUCTIONS})

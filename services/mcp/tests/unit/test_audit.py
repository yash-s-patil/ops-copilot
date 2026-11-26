"""What is recorded about each tool call."""

import asyncio
import contextlib
import json
from typing import Any

import pytest
import structlog
from fastmcp.exceptions import McpError

from opscp_common.errors import ErrorCode
from opscp_common.hashing import content_hash
from opscp_common.limits import Limits
from opscp_mcp.authz.identity import Identity
from opscp_mcp.ports.controls import AuditEntry
from opscp_mcp.services.orders import OrderService
from opscp_mcp_testing.fakes.controls import FakeAuditTrail
from opscp_mcp_testing.identities import ARJUN, PRIYA, SAM
from opscp_mcp_testing.server import (
    SignedOut,
    call,
    call_with,
    connected,
    data_of,
    error_of,
    server_for,
)
from opscp_mcp_testing.world import ASHA, NOW, SOUTH_ORDER, STUCK_ON_ADDRESS

CUSTOMER_EMAIL = "asha.rao@example.in"


async def _recorded(
    identity: Identity, limits: Limits, tool: str, arguments: dict[str, Any], **changed: object
) -> tuple[Any, AuditEntry]:
    audit = FakeAuditTrail()
    async with connected(server_for(identity, limits, audit=audit, **changed)) as client:
        result = await call_with(client, tool, arguments)
    (entry,) = audit.entries
    return result, entry


async def test_a_call_that_succeeds_is_recorded_with_who_what_and_the_result(
    limits: Limits,
) -> None:
    result, entry = await _recorded(
        PRIYA, limits, "get_order", {"order_id": STUCK_ON_ADDRESS, "events": "none"}
    )

    assert data_of(result)["order_id"] == STUCK_ON_ADDRESS
    assert entry.model_dump(exclude={"duration_ms", "result_bytes"}) == {
        "trace_id": None,
        "run_id": None,
        "actor_oid": PRIYA.oid,
        "actor_roles": ("Support.L1",),
        "actor_kind": "user",
        "target": "get_order",
        "action": "call",
        "args_redacted": {"order_id": STUCK_ON_ADDRESS, "events": "none"},
        "outcome": "ok",
        "error_code": None,
        "result_hash": content_hash(result.structured_content),
    }
    assert entry.result_bytes == len(result.content[0].text.encode())
    assert entry.duration_ms >= 0


async def test_a_call_refused_for_lack_of_permission_is_recorded_as_a_denial(
    limits: Limits,
) -> None:
    result, entry = await _recorded(SAM, limits, "get_order", {"order_id": STUCK_ON_ADDRESS})

    assert error_of(result).code is ErrorCode.PERMISSION_DENIED
    assert (entry.action, entry.outcome, entry.error_code) == (
        "deny",
        "denied",
        "PERMISSION_DENIED",
    )
    assert (entry.actor_oid, entry.actor_roles) == (SAM.oid, ("Platform.Admin",))
    assert (entry.result_hash, entry.result_bytes) == (None, None)


@pytest.mark.parametrize(
    ("arguments", "code"),
    [
        ({"order_id": SOUTH_ORDER}, "NOT_FOUND"),
        ({"order_id": "not a number"}, "VALIDATION_FAILED"),
        ({}, "VALIDATION_FAILED"),
    ],
    ids=["not visible to the caller", "wrong type", "missing argument"],
)
async def test_a_call_that_fails_is_recorded_with_its_error_code(
    limits: Limits, arguments: dict[str, Any], code: str
) -> None:
    result, entry = await _recorded(PRIYA, limits, "get_order", arguments)

    assert error_of(result).code.value == code
    assert (entry.action, entry.outcome, entry.error_code) == ("call", "error", code)


class _Broken:
    async def get(self, *_: object, **__: object) -> None:
        raise RuntimeError(f"could not load the order of {CUSTOMER_EMAIL}")


async def test_a_defect_is_recorded_as_internal_without_its_text(limits: Limits) -> None:
    orders = OrderService(_Broken(), limits, lambda: NOW)

    _, entry = await _recorded(
        PRIYA, limits, "get_order", {"order_id": STUCK_ON_ADDRESS}, orders=orders
    )

    assert (entry.outcome, entry.error_code) == ("error", "INTERNAL")
    assert CUSTOMER_EMAIL not in entry.model_dump_json()


async def test_a_tool_that_does_not_exist_is_recorded_without_the_callers_text(
    limits: Limits,
) -> None:
    _, entry = await _recorded(
        PRIYA, limits, f"no_such_tool {CUSTOMER_EMAIL}", {"email": CUSTOMER_EMAIL}
    )

    assert (entry.target, entry.args_redacted) == ("an unknown tool", {})
    assert (entry.outcome, entry.error_code) == ("error", "NOT_FOUND")


async def test_only_the_arguments_the_tool_declares_are_recorded(limits: Limits) -> None:
    _, entry = await _recorded(
        PRIYA,
        limits,
        "get_order",
        {"order_id": STUCK_ON_ADDRESS, "asha_rao_at_example_in": 1, "email": CUSTOMER_EMAIL},
    )

    assert entry.args_redacted == {"order_id": STUCK_ON_ADDRESS}
    assert entry.error_code == "VALIDATION_FAILED"


@pytest.mark.parametrize("identity", [SAM, ARJUN], ids=["a denial", "a lookup"])
async def test_an_argument_the_database_cannot_store_does_not_stop_the_record(
    limits: Limits, identity: Identity
) -> None:
    _, entry = await _recorded(
        identity, limits, "lookup_customer", {"email": f"{CUSTOMER_EMAIL}\x00"}
    )

    assert entry.outcome in ("denied", "error")
    assert "\x00" not in entry.model_dump_json()


class _Abandoned:
    """An order lookup whose caller goes away while it is running."""

    def __init__(self) -> None:
        self.running = asyncio.Event()

    async def get(self, *_: object, **__: object) -> None:
        self.running.set()
        await asyncio.sleep(3600)


async def test_a_call_the_client_abandons_is_still_recorded(limits: Limits) -> None:
    audit, lookup = FakeAuditTrail(), _Abandoned()
    orders = OrderService(lookup, limits, lambda: NOW)

    async with connected(server_for(PRIYA, limits, audit=audit, orders=orders)) as client:
        abandoned = asyncio.create_task(call(client, "get_order", order_id=STUCK_ON_ADDRESS))
        await lookup.running.wait()
        abandoned.cancel()
        with pytest.raises(asyncio.CancelledError):
            await abandoned
        for _ in range(100):
            if audit.entries:
                break
            await asyncio.sleep(0.01)

    (entry,) = audit.entries
    assert (entry.target, entry.outcome, entry.error_code) == ("get_order", "error", "CANCELLED")


class _RaisesAProtocolError:
    async def get(self, *_: object, **__: object) -> None:
        raise McpError(-32021, f"secret text about {CUSTOMER_EMAIL}")


async def test_a_protocol_error_from_inside_a_tool_is_recorded(limits: Limits) -> None:
    audit = FakeAuditTrail()
    orders = OrderService(_RaisesAProtocolError(), limits, lambda: NOW)

    async with connected(server_for(PRIYA, limits, audit=audit, orders=orders)) as client:
        with contextlib.suppress(McpError):
            await call(client, "get_order", order_id=STUCK_ON_ADDRESS)

    (entry,) = audit.entries
    assert (entry.outcome, entry.error_code) == ("error", "INTERNAL")


async def test_contact_details_in_arguments_never_reach_the_record_or_the_log(
    limits: Limits,
) -> None:
    with structlog.testing.capture_logs() as logs:
        result, entry = await _recorded(ARJUN, limits, "lookup_customer", {"email": CUSTOMER_EMAIL})

    assert data_of(result)["customer_id"] == ASHA
    assert str(entry.args_redacted["email"]).startswith("<email:")
    assert CUSTOMER_EMAIL not in entry.model_dump_json() + json.dumps(logs)


async def test_the_log_line_has_the_same_fields_as_the_record(limits: Limits) -> None:
    with structlog.testing.capture_logs() as logs:
        _, entry = await _recorded(PRIYA, limits, "get_order", {"order_id": STUCK_ON_ADDRESS})

    (line,) = [line for line in logs if line["event"] == "audit"]
    assert line == entry.model_dump(mode="json") | {
        "event": "audit",
        "log_level": "info",
        "audit_id": 1,
    }


async def test_a_result_too_large_to_return_is_recorded_as_the_error_the_caller_got(
    limits: Limits,
) -> None:
    small = limits.model_copy(
        update={"tool": limits.tool.model_copy(update={"result_bytes_max": 10})}
    )

    result, entry = await _recorded(PRIYA, small, "get_order", {"order_id": STUCK_ON_ADDRESS})

    assert error_of(result).code is ErrorCode.INTERNAL
    assert (entry.outcome, entry.error_code, entry.result_hash) == ("error", "INTERNAL", None)


async def test_a_request_with_no_caller_is_refused_before_anything_is_recorded(
    limits: Limits,
) -> None:
    audit = FakeAuditTrail()

    async with connected(server_for(PRIYA, limits, audit=audit, identities=SignedOut())) as client:
        body = error_of(await call(client, "get_order", order_id=STUCK_ON_ADDRESS))

    assert body.code is ErrorCode.PERMISSION_DENIED
    assert audit.entries == []


class _Unwritable:
    async def record(self, _entry: AuditEntry) -> int:
        raise ConnectionError(f"audit database down for {CUSTOMER_EMAIL}")


class _Stalled:
    async def record(self, _entry: AuditEntry) -> int:
        await asyncio.sleep(3600)
        raise AssertionError


async def test_a_result_is_withheld_when_it_cannot_be_recorded(limits: Limits) -> None:
    async with connected(server_for(PRIYA, limits, audit=_Unwritable())) as client:
        body = error_of(await call(client, "get_order", order_id=STUCK_ON_ADDRESS))

    assert (body.code, body.details) == (ErrorCode.INTERNAL, {})
    assert CUSTOMER_EMAIL not in body.model_dump_json()


async def test_a_denial_that_cannot_be_recorded_is_reported_as_the_recording_failure(
    limits: Limits,
) -> None:
    with structlog.testing.capture_logs() as logs:
        async with connected(server_for(SAM, limits, audit=_Unwritable())) as client:
            result = await call(client, "get_order", order_id=STUCK_ON_ADDRESS)

    (failure,) = [line for line in logs if line["event"] == "request_failed"]
    assert error_of(result).code is ErrorCode.INTERNAL
    assert failure["error"] == "ConnectionError"
    assert any(frame.endswith("in record") for frame in failure["where"])


async def test_recording_is_given_up_after_the_database_time_limit(limits: Limits) -> None:
    quick = limits.model_copy(
        update={"timeout": limits.timeout.model_copy(update={"postgres_s": 0})}
    )

    async with connected(server_for(PRIYA, quick, audit=_Stalled())) as client:
        body = error_of(await call(client, "get_order", order_id=STUCK_ON_ADDRESS))

    assert (body.code, body.retryable) == (ErrorCode.TIMEOUT, True)


async def test_reading_a_resource_or_a_prompt_or_a_list_is_not_a_tool_call(limits: Limits) -> None:
    audit = FakeAuditTrail()

    async with connected(server_for(PRIYA, limits, audit=audit)) as client:
        await client.list_tools()
        await client.read_resource("meridian://runbooks/index")
        await client.get_prompt("investigate_order", {"order_id": STUCK_ON_ADDRESS})

    assert audit.entries == []

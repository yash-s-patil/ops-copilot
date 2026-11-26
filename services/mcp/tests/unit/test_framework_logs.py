import io
import json
import logging
from collections.abc import Iterator

import pytest
import structlog
from fastmcp.exceptions import McpError

from opscp_common.limits import Limits
from opscp_common.logging import configure_logging
from opscp_mcp.services.orders import OrderService
from opscp_mcp.telemetry.logs import (
    EVENT_STREAM,
    FRAMEWORK,
    REQUEST_RUNNER,
    TRANSPORT,
    OneReportPerFailure,
    adopt_framework_logs,
)
from opscp_mcp_testing.identities import PRIYA, SAM
from opscp_mcp_testing.server import call, call_with, connected, error_of, server_for
from opscp_mcp_testing.world import NOW, STUCK_ON_ADDRESS

CUSTOMER_EMAIL = "priya@example.in"


@pytest.fixture
def production_log() -> Iterator[io.StringIO]:
    """Logging set up as the running server has it, written to a buffer the test can read."""
    framework, runner, transport = (
        logging.getLogger(name) for name in (FRAMEWORK, REQUEST_RUNNER, TRANSPORT)
    )
    before = (framework.handlers[:], framework.propagate, transport.level)
    configure_logging(service="mcp", level="INFO", json=True)
    adopt_framework_logs()
    written = io.StringIO()
    (configured,) = logging.getLogger().handlers
    readable = logging.StreamHandler(written)
    readable.setFormatter(configured.formatter)
    logging.getLogger().handlers = [readable]
    yield written
    framework.handlers[:], framework.propagate = before[0], before[1]
    transport.setLevel(before[2])
    for added in [f for f in runner.filters if isinstance(f, OneReportPerFailure)]:
        runner.removeFilter(added)
    structlog.reset_defaults()
    logging.basicConfig(handlers=[logging.NullHandler()], level=logging.WARNING, force=True)
    logging.getLogger("azure").setLevel(logging.NOTSET)


class _Leaky:
    async def get(self, *_: object, **__: object) -> None:
        raise RuntimeError(f"could not load the order of {CUSTOMER_EMAIL}")


async def test_a_failed_call_is_logged_as_json_without_the_exception_text(
    limits: Limits, production_log: io.StringIO
) -> None:
    orders = OrderService(_Leaky(), limits, lambda: NOW)

    async with connected(server_for(PRIYA, limits, orders=orders)) as client:
        result = await call(client, "get_order", order_id=STUCK_ON_ADDRESS)

    logged = production_log.getvalue()
    lines = [json.loads(line) for line in logged.splitlines()]
    assert error_of(result).code.value == "INTERNAL"
    assert CUSTOMER_EMAIL not in logged
    assert "Traceback" not in logged
    (failure,) = [line for line in lines if line["event"] == "request_failed"]
    assert (failure["during"], failure["error"], failure["service"]) == (
        "get_order",
        "RuntimeError",
        "mcp",
    )
    assert failure["where"][-1].endswith("in get")
    assert [line["event"] for line in lines] == ["request_failed"]


@pytest.mark.usefixtures("production_log")
def test_the_framework_logs_through_the_one_handler() -> None:
    framework = logging.getLogger(FRAMEWORK)

    assert (framework.handlers, framework.propagate) == ([], True)
    assert logging.getLogger(TRANSPORT).getEffectiveLevel() == logging.WARNING


def test_libraries_that_log_whole_responses_stay_quiet_at_debug_level() -> None:
    quiet = [logging.getLogger(name) for name in (TRANSPORT, EVENT_STREAM)]
    before = [logger.level for logger in quiet]
    configure_logging(service="mcp", level="DEBUG", json=True)
    adopt_framework_logs()

    try:
        assert [logger.isEnabledFor(logging.INFO) for logger in quiet] == [False, False]
        assert logging.getLogger("sse_starlette.sse").getEffectiveLevel() == logging.WARNING
    finally:
        for logger, level in zip(quiet, before, strict=True):
            logger.setLevel(level)
        structlog.reset_defaults()
        logging.basicConfig(handlers=[logging.NullHandler()], level=logging.WARNING, force=True)
        logging.getLogger("azure").setLevel(logging.NOTSET)


@pytest.mark.usefixtures("production_log")
def test_adopting_twice_adds_the_filter_once() -> None:
    adopt_framework_logs()

    runner = logging.getLogger(REQUEST_RUNNER)
    assert len([f for f in runner.filters if isinstance(f, OneReportPerFailure)]) == 1


def _record(message: str) -> logging.LogRecord:
    try:
        raise RuntimeError(CUSTOMER_EMAIL)
    except RuntimeError as error:
        failure = (type(error), error, error.__traceback__)
    record = logging.LogRecord(REQUEST_RUNNER, logging.ERROR, __file__, 1, message, None, failure)
    record.exc_text = CUSTOMER_EMAIL
    return record


async def test_nothing_the_caller_sent_is_logged_for_a_refused_call(
    limits: Limits, production_log: io.StringIO
) -> None:
    async with connected(server_for(PRIYA, limits)) as client:
        await call_with(client, f"no_such_tool {CUSTOMER_EMAIL}", {})
        await call_with(client, "get_order", {"order_id": CUSTOMER_EMAIL})
        await call_with(client, "get_order", {"order_id": 900101, CUSTOMER_EMAIL: "x"})
        await call(client, "lookup_customer", email=CUSTOMER_EMAIL)
        await call(client, "lookup_customer", email="nobody@example.in")

    logged = production_log.getvalue()
    lines = [json.loads(line) for line in logged.splitlines()]
    assert CUSTOMER_EMAIL not in logged
    assert "nobody@example.in" not in logged
    assert [(line["event"], line["during"], line["code"]) for line in lines] == [
        ("request_refused", "an unknown tool", "NOT_FOUND"),
        ("request_refused", "get_order", "VALIDATION_FAILED"),
        ("request_refused", "get_order", "VALIDATION_FAILED"),
        ("request_refused", "lookup_customer", "NOT_FOUND"),
        ("request_refused", "lookup_customer", "NOT_FOUND"),
    ]


class _HandlesOneFailureThenHitsAnother:
    async def get(self, *_: object, **__: object) -> None:
        try:
            raise KeyError("handled")
        except KeyError:
            raise ValueError("the real failure") from None


async def test_the_failure_logged_is_the_one_that_was_not_handled(
    limits: Limits, production_log: io.StringIO
) -> None:
    orders = OrderService(_HandlesOneFailureThenHitsAnother(), limits, lambda: NOW)

    async with connected(server_for(PRIYA, limits, orders=orders)) as client:
        await call(client, "get_order", order_id=STUCK_ON_ADDRESS)

    (line,) = [json.loads(line) for line in production_log.getvalue().splitlines()]
    assert (line["event"], line["error"]) == ("request_failed", "ValueError")


async def test_a_refused_resource_and_prompt_are_each_logged_once(
    limits: Limits, production_log: io.StringIO
) -> None:
    async with connected(server_for(SAM, limits)) as sam:
        with pytest.raises(McpError):
            await sam.read_resource("meridian://runbooks/index")
        with pytest.raises(McpError):
            await sam.get_prompt("investigate_order", {"order_id": 4471})

    lines = [json.loads(line) for line in production_log.getvalue().splitlines()]
    assert [(line["event"], line["level"], line["code"]) for line in lines] == [
        ("request_refused", "info", "PERMISSION_DENIED"),
        ("request_refused", "info", "PERMISSION_DENIED"),
    ]


@pytest.mark.parametrize(
    "message",
    [
        "Error calling tool 'get_order'",
        "Invalid arguments for tool 'get_order': {}",
        "Error reading resource 'meridian://runbooks/index'",
        "Error rendering prompt 'investigate_order'",
    ],
)
def test_the_frameworks_line_for_a_failed_request_is_dropped(message: str) -> None:
    assert OneReportPerFailure().filter(_record(message)) is False


def test_any_other_framework_line_is_kept_without_its_exception() -> None:
    record = _record("Failed to start the session manager")

    assert OneReportPerFailure().filter(record) is True
    assert (record.exc_info, record.exc_text) == (None, None)
    assert record.getMessage() == "Failed to start the session manager"

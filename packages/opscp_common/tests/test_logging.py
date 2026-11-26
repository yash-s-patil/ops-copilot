import json
import logging
from collections.abc import Iterator

import pytest
import structlog

from opscp_common.logging import configure_logging


@pytest.fixture(autouse=True)
def reset_logging() -> Iterator[None]:
    yield
    structlog.contextvars.clear_contextvars()
    structlog.reset_defaults()
    logging.basicConfig(handlers=[logging.NullHandler()], level=logging.WARNING, force=True)


def _lines(capsys: pytest.CaptureFixture[str]) -> list[dict[str, str]]:
    return [json.loads(line) for line in capsys.readouterr().err.splitlines()]


def test_json_lines_carry_the_service_level_time_and_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(service="mcp", level="INFO", json=True)

    structlog.get_logger().info("tool_called", tool="get_order")

    (line,) = _lines(capsys)
    assert line.pop("timestamp").endswith("Z")
    assert line == {"event": "tool_called", "tool": "get_order", "level": "info", "service": "mcp"}


def test_lines_below_the_level_are_dropped(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(service="mcp", level="WARNING", json=True)

    structlog.get_logger().info("quiet")
    structlog.get_logger().warning("loud")

    assert [line["event"] for line in _lines(capsys)] == ["loud"]


def test_library_logs_take_the_same_shape(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(service="mcp", level="INFO", json=True)

    logging.getLogger("sqlalchemy.engine").warning("pool exhausted")

    (line,) = _lines(capsys)
    assert (line["event"], line["level"], line["service"]) == ("pool exhausted", "warning", "mcp")


def test_the_service_survives_a_per_request_context_reset(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(service="mcp", level="INFO", json=True)

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="r1")
    structlog.get_logger().info("handled")

    (line,) = _lines(capsys)
    assert (line["service"], line["request_id"]) == ("mcp", "r1")


def test_an_exception_is_logged_as_one_line(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(service="mcp", level="INFO", json=True)

    try:
        raise RuntimeError("boom")
    except RuntimeError:
        structlog.get_logger().exception("failed")

    (line,) = _lines(capsys)
    assert line["event"] == "failed"
    assert "RuntimeError: boom" in line["exception"]


def test_terminal_output_is_readable_text(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(service="seed", level="INFO", json=False)

    structlog.get_logger().info("seeded", customers=500)

    output = capsys.readouterr().err
    assert "seeded" in output
    assert "customers" in output
    assert "\x1b[" not in output
    with pytest.raises(json.JSONDecodeError):
        json.loads(output)

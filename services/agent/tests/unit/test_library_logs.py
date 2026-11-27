import logging
from collections.abc import Iterator

import pytest

from opscp_agent.telemetry.logs import FRAMEWORK, HELD_AT_WARNING, adopt_library_logs
from opscp_common.logging import configure_logging


@pytest.fixture(autouse=True)
def logging_as_it_was() -> Iterator[None]:
    root = logging.getLogger()
    kept = (root.handlers[:], root.level)
    levels = {name: logging.getLogger(name).level for name in (*HELD_AT_WARNING, FRAMEWORK)}
    framework = logging.getLogger(FRAMEWORK)
    held = (framework.handlers[:], framework.propagate)
    yield
    root.handlers[:], root.level = kept[0], kept[1]
    framework.handlers[:], framework.propagate = held
    for name, level in levels.items():
        logging.getLogger(name).setLevel(level)


def test_the_libraries_that_log_addresses_and_responses_are_held_at_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(service="agent", level="DEBUG", json=True)
    adopt_library_logs()

    logging.getLogger("httpx2").info('HTTP Request: GET http://mcp/approvals?ticket_id=2207 "200"')
    logging.getLogger("mcp.client.streamable_http").debug("response: ananya.k@example.in")
    logging.getLogger("httpx2").warning("connection pool is full")

    written = capsys.readouterr().err
    assert "ticket_id" not in written
    assert "ananya" not in written
    assert "connection pool is full" in written


def test_the_mcp_library_writes_in_the_service_s_format_and_without_exception_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(service="agent", level="INFO", json=True)
    adopt_library_logs()
    adopt_library_logs()

    try:
        raise ValueError("the token was eyJhbGciOi.secret")
    except ValueError:
        logging.getLogger("fastmcp.client").exception("Bearer token rejected")

    written = capsys.readouterr().err.strip().splitlines()
    assert len(written) == 1
    assert '"service": "agent"' in written[0]
    assert "Bearer token rejected" in written[0]
    assert "eyJ" not in written[0]
    assert all(len(handler.filters) == 1 for handler in logging.getLogger().handlers)

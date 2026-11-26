import json
from typing import Any

import pytest
import uvicorn
from fastmcp.tools import ToolResult

from opscp_mcp import __main__ as entry_point
from opscp_mcp.middleware.envelope import content_bytes


def test_the_web_server_is_started_so_that_no_request_target_is_logged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[tuple[str, dict[str, Any]]] = []

    def run(application: str, **options: Any) -> None:
        started.append((application, options))

    monkeypatch.setattr(uvicorn, "run", run)

    entry_point.main()

    ((application, options),) = started
    assert application == "opscp_mcp.main:create_app"
    assert options == {
        "factory": True,
        "host": "0.0.0.0",  # noqa: S104 -- the value under test
        "port": 8000,
        "log_config": None,
        "access_log": False,
        "ws": "none",
        "server_header": False,
    }


@pytest.mark.parametrize(
    ("text", "structured", "size"),
    [
        pytest.param("x" * 500, {"a": 1}, 500, id="the text is larger"),
        pytest.param("x", {"a": "y" * 500}, len('{"a":""}') + 500, id="the structure is larger"),
        pytest.param("नीति", {"a": 1}, len("नीति".encode()), id="counted in bytes"),
    ],
)
def test_a_result_is_measured_by_its_larger_form(
    text: str, structured: dict[str, object], size: int
) -> None:
    result = ToolResult(content=text, structured_content=structured)

    assert content_bytes(result) == size
    assert size >= len(json.dumps(structured, separators=(",", ":")))

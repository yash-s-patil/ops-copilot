import base64
import json
import ssl
from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from opscp_common.limits import Limits
from opscp_common.settings import DatabaseSettings, Environment
from opscp_mcp import main
from opscp_mcp.container import Services
from opscp_mcp.main import (
    MCP_PATH,
    NotReadyForDeploymentError,
    build_app,
    create_app,
    database_engine,
)
from opscp_mcp.settings import McpSettings
from opscp_mcp_testing.identities import PRIYA
from opscp_mcp_testing.paths import REPO_ROOT, RUNBOOKS_DIR
from opscp_mcp_testing.server import fake_services
from opscp_mcp_testing.world import STUCK_ON_ADDRESS

NOWHERE = "postgresql+asyncpg://nobody:unused@127.0.0.1:1/nothing"
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}
MCP_REVISION = "2026-07-28"
_KEY = base64.b64encode(b"not a real key, only a test value").decode()
ENVIRONMENT = {
    "ENVIRONMENT": "local",
    "LIMITS_FILE": str(REPO_ROOT / "config" / "limits.yaml"),
    "RUNBOOKS_DIR": str(RUNBOOKS_DIR),
    "DATABASE_HOST": "127.0.0.1",
    "DATABASE_PORT": "1",
    "DATABASE_NAME": "nothing",
    "DATABASE_USER": "nobody",
    "DATABASE_PASSWORD": "unused",
    "POLICIES_CONTAINER": "policies",
    "BLOB_CONNECTION_STRING": (
        f"DefaultEndpointsProtocol=http;AccountName=opscplocal;AccountKey={_KEY};"
        "BlobEndpoint=http://127.0.0.1:10000/opscplocal;"
    ),
}


def _rpc(http: TestClient, method: str, params: dict[str, object]) -> dict[str, Any]:
    response = http.post(
        MCP_PATH,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        headers=MCP_HEADERS,
    )
    assert response.status_code == 200, response.text
    (data,) = [line for line in response.text.splitlines() if line.startswith("data: ")]
    return json.loads(data.removeprefix("data: "))["result"]


@pytest.fixture
def engine() -> AsyncEngine:
    return create_async_engine(NOWHERE)


@pytest.fixture
def http(engine: AsyncEngine, limits: Limits) -> Iterator[TestClient]:
    services = fake_services(PRIYA, limits)

    @asynccontextmanager
    async def open_services() -> AsyncGenerator[Services]:
        yield services

    with TestClient(build_app(engine, open_services, limits)) as client:
        yield client


def test_the_process_reports_healthy_without_any_backend(http: TestClient) -> None:
    response = http.get("/healthz")

    assert (response.status_code, response.json()) == (200, {"status": "ok"})


def test_the_process_is_not_ready_while_the_database_cannot_be_reached(http: TestClient) -> None:
    response = http.get("/readyz")

    assert (response.status_code, response.json()) == (503, {"status": "unavailable"})
    assert "127.0.0.1" not in response.text


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/admin", "/"])
def test_nothing_else_is_served_outside_the_mcp_endpoint(http: TestClient, path: str) -> None:
    assert http.get(path).status_code == 404


def test_the_tools_are_listed_over_http(http: TestClient) -> None:
    result = _rpc(http, "tools/list", {})

    assert [tool["name"] for tool in result["tools"]] == [
        "lookup_customer",
        "get_order",
        "list_tickets",
        "get_ticket",
        "search_runbooks",
        "get_policy_document",
    ]


def test_a_tool_is_called_over_http(http: TestClient) -> None:
    result = _rpc(
        http, "tools/call", {"name": "get_order", "arguments": {"order_id": STUCK_ON_ADDRESS}}
    )

    assert result["structuredContent"]["data"]["status"] == "packed"


def test_each_request_is_served_without_a_session(http: TestClient) -> None:
    first = http.post(
        MCP_PATH,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers=MCP_HEADERS,
    )

    assert first.status_code == 200
    assert "mcp-session-id" not in {name.lower() for name in first.headers}


def test_the_endpoint_answers_at_its_exact_path_without_a_redirect(http: TestClient) -> None:
    response = http.post(
        MCP_PATH,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers=MCP_HEADERS | {"Host": "elsewhere.example"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "location" not in {name.lower() for name in response.headers}


def test_the_server_speaks_the_current_protocol_and_names_its_own_version(
    http: TestClient,
) -> None:
    response = http.post(
        MCP_PATH,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "server/discover",
            "params": {
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": MCP_REVISION,
                    "io.modelcontextprotocol/clientCapabilities": {},
                }
            },
        },
        headers=MCP_HEADERS
        | {"MCP-Protocol-Version": MCP_REVISION, "Mcp-Method": "server/discover"},
    )

    result = response.json()["result"]
    assert result["supportedVersions"] == [MCP_REVISION]
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"] == {
        "name": "Meridian Ops Copilot",
        "version": "0.1.0",
    }


@pytest.mark.parametrize("path", ["/mcp/", "/mcp//", "/mcp/extra"])
def test_a_path_near_the_endpoint_is_not_found_and_never_redirects(
    http: TestClient, path: str
) -> None:
    response = http.post(
        path,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers=MCP_HEADERS | {"Host": "elsewhere.example"},
        follow_redirects=False,
    )

    assert response.status_code == 404
    assert "location" not in {name.lower() for name in response.headers}


@pytest.mark.parametrize("method", ["GET", "DELETE", "PUT"])
def test_the_endpoint_accepts_only_posts(http: TestClient, method: str) -> None:
    assert http.request(method, MCP_PATH, headers=MCP_HEADERS).status_code == 405


def test_the_engine_is_disposed_when_the_application_stops(
    limits: Limits, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine(NOWHERE)
    disposed: list[bool] = []

    async def dispose(self: AsyncEngine, close: bool = True) -> None:
        disposed.append(close)

    monkeypatch.setattr(AsyncEngine, "dispose", dispose)

    @asynccontextmanager
    async def open_services() -> AsyncGenerator[Services]:
        yield fake_services(PRIYA, limits)

    with TestClient(build_app(engine, open_services, limits)) as client:
        client.get("/healthz")
        assert disposed == []

    assert disposed == [True]


@pytest.fixture
def logging_setup(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Records how logging would be set up, without changing it for the other tests."""
    calls: list[dict[str, object]] = []

    def record(**setup: object) -> None:
        calls.append(setup)

    monkeypatch.setattr(main, "configure_logging", record)
    monkeypatch.setattr(main, "adopt_framework_logs", lambda: calls.append({"adopted": True}))
    return calls


@pytest.fixture
def configured(
    monkeypatch: pytest.MonkeyPatch, logging_setup: list[dict[str, object]]
) -> pytest.MonkeyPatch:
    for name in ("BLOB_ACCOUNT_URL", "AZURE_CLIENT_ID", "LOG_LEVEL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


def test_logging_is_set_up_for_the_environment(
    configured: pytest.MonkeyPatch, logging_setup: list[dict[str, object]]
) -> None:
    create_app()
    configured.setenv("LOG_LEVEL", "WARNING")
    create_app()

    assert logging_setup == [
        {"service": "mcp", "level": "INFO", "json": False},
        {"adopted": True},
        {"service": "mcp", "level": "WARNING", "json": False},
        {"adopted": True},
    ]


def test_logs_are_json_everywhere_but_locally(configured: pytest.MonkeyPatch) -> None:
    local = McpSettings()  # pyright: ignore[reportCallIssue] -- fields come from the environment
    configured.setenv("ENVIRONMENT", "prod")
    deployed = McpSettings()  # pyright: ignore[reportCallIssue] -- fields come from the environment

    assert (local.json_logs, deployed.json_logs) == (False, True)


@pytest.mark.parametrize("environment", ["demo", "prod"])
def test_the_server_refuses_to_start_outside_local_until_it_can_verify_callers(
    configured: pytest.MonkeyPatch, environment: str
) -> None:
    configured.setenv("ENVIRONMENT", environment)
    configured.setenv("BLOB_CONNECTION_STRING", "")
    configured.setenv("BLOB_ACCOUNT_URL", "https://stopscpdemo.blob.core.windows.net")
    configured.setenv("AZURE_CLIENT_ID", "7f1c2d3e-0000-4000-8000-0000000000aa")

    with pytest.raises(NotReadyForDeploymentError, match=environment):
        create_app()


def _database() -> DatabaseSettings:
    return DatabaseSettings.model_validate(
        {"host": "db.internal", "name": "meridian", "user": "mcp_app", "password": "unused"}
    )


@pytest.mark.parametrize("environment", ["demo", "prod"])
def test_the_database_connection_is_encrypted_and_verified_outside_local(
    environment: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    made: list[tuple[dict[str, object], bool]] = []

    def record(url: object, *, hide_parameters: bool, connect_args: dict[str, object]) -> object:
        made.append((connect_args, hide_parameters))
        return object()

    monkeypatch.setattr(main, "create_async_engine", record)

    database_engine(_database(), environment)
    database_engine(_database(), "local")

    (deployed, _), (local, _) = made
    context = deployed["ssl"]
    assert isinstance(context, ssl.SSLContext)
    assert (context.verify_mode, context.check_hostname) == (ssl.CERT_REQUIRED, True)
    assert context.cert_store_stats()["x509_ca"] > 0
    assert local == {}
    assert [hidden for _, hidden in made] == [True, True]


def test_the_application_starts_from_its_environment_and_serves_nobody(
    configured: pytest.MonkeyPatch,
) -> None:
    with TestClient(create_app()) as http:
        assert http.get("/healthz").json() == {"status": "ok"}
        assert http.get("/readyz").status_code == 503
        result = _rpc(
            http, "tools/call", {"name": "get_order", "arguments": {"order_id": STUCK_ON_ADDRESS}}
        )

    assert result["isError"] is True
    (block,) = result["content"]
    body = json.loads(block["text"])
    assert body["code"] == "PERMISSION_DENIED"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ENVIRONMENT", "staging"),
        ("LIMITS_FILE", "/nonexistent/limits.yaml"),
        ("RUNBOOKS_DIR", "/nonexistent/runbooks"),
        ("LOG_LEVEL", "LOUD"),
        ("DATABASE_PORT", "70000"),
        ("POLICIES_CONTAINER", ""),
    ],
)
def test_a_wrong_setting_stops_the_application_before_it_serves(
    configured: pytest.MonkeyPatch, name: str, value: str
) -> None:
    configured.setenv(name, value)

    with pytest.raises(ValidationError):
        create_app()


@pytest.mark.parametrize("name", ["ENVIRONMENT", "LIMITS_FILE", "RUNBOOKS_DIR", "DATABASE_HOST"])
def test_a_missing_setting_stops_the_application_before_it_serves(
    configured: pytest.MonkeyPatch, name: str
) -> None:
    configured.delenv(name)

    with pytest.raises(ValidationError):
        create_app()

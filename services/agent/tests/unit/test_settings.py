"""What the service will and will not start with."""

import base64
from typing import Any

import pytest
import uvicorn
from pydantic import ValidationError

from opscp_agent import __main__ as entry_point
from opscp_agent.main import create_app
from opscp_agent.settings import (
    AgentSettings,
    DevSignIn,
    EntraSignIn,
    FederatedExchange,
    SecretExchange,
)
from opscp_mcp_testing.paths import REPO_ROOT
from opscp_mcp_testing.signin import ISSUER, public_key_setting

TENANT = "7f1c2d3e-0000-4000-8000-0000000000bb"
CLIENT = "7f1c2d3e-0000-4000-8000-0000000000cc"
IDENTITY = "7f1c2d3e-0000-4000-8000-0000000000aa"
LOCAL: dict[str, str] = {
    "ENVIRONMENT": "local",
    "LIMITS_FILE": str(REPO_ROOT / "config" / "limits.yaml"),
    "PRICING_FILE": str(REPO_ROOT / "config" / "pricing.yaml"),
    "USD_INR_RATE": "89.5",
    "AUTH_MODE": "dev",
    "AGENT_API_AUDIENCE": "api://opscp-agent",
    "AGENT_API_CLIENT_ID": "opscp-agent-api",
    "DEV_ISSUER": ISSUER,
    "DEV_ISSUER_PUBLIC_KEY": public_key_setting(),
    "MCP_URL": "http://mcp:8000",
    "MCP_SCOPE": "api://opscp-mcp/.default",
    "OBO_CREDENTIAL": "secret",
    "TOKEN_URL": "http://dev-issuer:8090/token",
    "AGENT_CLIENT_SECRET": "s" * 48,
    "LLM_PROVIDER": "scripted",
    "CANARY": "canary-0123456789abcdef",
    "DATABASE_HOST": "127.0.0.1",
    "DATABASE_PORT": "1",
    "DATABASE_NAME": "copilot",
    "DATABASE_USER": "agent_app",
    "DATABASE_PASSWORD": "unused",
}
DEPLOYED = LOCAL | {
    "ENVIRONMENT": "demo",
    "AUTH_MODE": "entra",
    "TENANT_ID": TENANT,
    "AGENT_API_CLIENT_ID": CLIENT,
    "AZURE_CLIENT_ID": IDENTITY,
    "DEV_ISSUER": "",
    "DEV_ISSUER_PUBLIC_KEY": "",
    "MCP_URL": "https://mcp.internal.example",
    "OBO_CREDENTIAL": "federated",
    "TOKEN_URL": "",
    "AGENT_CLIENT_SECRET": "",
}


def _settings(environment: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> AgentSettings:
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    return AgentSettings()  # pyright: ignore[reportCallIssue] -- fields come from the environment


def test_a_laptop_signs_in_with_the_local_issuer_and_a_generated_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(LOCAL, monkeypatch)

    sign_in, exchange = settings.sign_in(), settings.token_exchange()

    assert isinstance(sign_in, DevSignIn)
    assert (sign_in.issuer, sign_in.audience) == (ISSUER, "api://opscp-agent")
    assert isinstance(exchange, SecretExchange)
    assert exchange.token_url == "http://dev-issuer:8090/token"  # noqa: S105 -- an address
    assert (settings.json_logs, settings.demo_tools) == (False, False)
    assert "s" * 48 not in repr(settings)
    assert "canary-0123456789abcdef" not in repr(settings)


def test_in_azure_the_service_holds_no_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(DEPLOYED, monkeypatch)

    sign_in, exchange = settings.sign_in(), settings.token_exchange()

    assert isinstance(sign_in, EntraSignIn)
    assert (str(sign_in.tenant_id), str(sign_in.client_id)) == (TENANT, CLIENT)
    assert isinstance(exchange, FederatedExchange)
    assert exchange.token_url == f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"
    assert (exchange.client_id, str(exchange.managed_identity_client_id)) == (CLIENT, IDENTITY)
    assert settings.json_logs is True


@pytest.mark.parametrize(
    ("changed", "message"),
    [
        (
            {"ENVIRONMENT": "demo", "MCP_URL": "https://mcp.example"},
            "AUTH_MODE=dev is allowed only when ENVIRONMENT is local",
        ),
        (
            {"ENVIRONMENT": "prod", "MCP_URL": "https://mcp.example"},
            "LLM_PROVIDER=scripted is never allowed",
        ),
        ({"ENVIRONMENT": "demo"}, "MCP_URL must be https"),
        ({"CHAOS_ENABLED": "true", "ENVIRONMENT": "prod"}, "CHAOS_ENABLED is never allowed"),
        ({"LLM_PROVIDER": "azure"}, "LLM_PROVIDER=azure is not available in this version"),
        ({"LLM_PROVIDER": "other"}, "llm_provider"),
        ({"DEV_ISSUER_PUBLIC_KEY": ""}, "AUTH_MODE=dev needs DEV_ISSUER"),
        ({"DEV_ISSUER_PUBLIC_KEY": "not base64!"}, "must be a base64-encoded PEM RSA public key"),
        (
            {"DEV_ISSUER_PUBLIC_KEY": base64.b64encode(b"not a key").decode()},
            "must be a base64-encoded PEM RSA public key",
        ),
        ({"AGENT_CLIENT_SECRET": ""}, "OBO_CREDENTIAL=secret needs TOKEN_URL"),
        ({"AGENT_CLIENT_SECRET": "short"}, "agent_client_secret"),
        ({"TOKEN_URL": ""}, "OBO_CREDENTIAL=secret needs TOKEN_URL"),
        ({"OBO_CREDENTIAL": "certificate"}, "OBO_CREDENTIAL=certificate is not available"),
        ({"OBO_CREDENTIAL": "federated"}, "OBO_CREDENTIAL=federated needs TENANT_ID"),
        ({"CANARY": "short"}, "canary"),
        ({"CANARY": ""}, "canary"),
        ({"USD_INR_RATE": "0"}, "usd_inr_rate"),
        ({"MCP_URL": "not a url"}, "mcp_url"),
        ({"LIMITS_FILE": "/nowhere/limits.yaml"}, "limits_file"),
    ],
)
def test_a_laptop_s_settings_that_do_not_hold_together_are_refused(
    monkeypatch: pytest.MonkeyPatch, changed: dict[str, str], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _settings(LOCAL | changed, monkeypatch)


@pytest.mark.parametrize(
    ("changed", "message"),
    [
        ({"MCP_URL": "http://mcp.internal.example"}, "MCP_URL must be https"),
        ({"OBO_CREDENTIAL": "secret", "AGENT_CLIENT_SECRET": "s" * 48}, "allowed only when"),
        ({"TENANT_ID": ""}, "AUTH_MODE=entra needs TENANT_ID"),
        ({"AGENT_API_CLIENT_ID": "opscp-agent-api"}, "AUTH_MODE=entra needs TENANT_ID"),
        ({"AZURE_CLIENT_ID": ""}, "OBO_CREDENTIAL=federated needs"),
        ({"ENVIRONMENT": "prod", "CHAOS_ENABLED": "true"}, "CHAOS_ENABLED is never allowed"),
    ],
)
def test_deployed_settings_that_do_not_hold_together_are_refused(
    monkeypatch: pytest.MonkeyPatch, changed: dict[str, str], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _settings(DEPLOYED | changed, monkeypatch)


def test_a_refusal_never_repeats_a_secret_it_was_given(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError) as refused:
        _settings(LOCAL | {"AGENT_CLIENT_SECRET": "tooshort-secret"}, monkeypatch)

    assert "tooshort-secret" not in str(refused.value)


def test_the_demonstration_switch_shows_the_demo_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _settings(LOCAL | {"CHAOS_ENABLED": "true"}, monkeypatch).demo_tools is True


def test_the_application_is_built_from_the_environment_without_reaching_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in LOCAL.items():
        monkeypatch.setenv(name, value)

    app = create_app()

    paths = set(app.openapi()["paths"])
    assert {"/healthz", "/readyz", "/api/me", "/api/runs", "/api/admin/runs"} <= paths
    assert "/api/runs/{run_id}/calls/{call_id}" not in paths


def test_the_web_server_is_started_so_that_no_request_target_is_logged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[tuple[str, dict[str, Any]]] = []

    def run(application: str, **options: Any) -> None:
        started.append((application, options))

    monkeypatch.setattr(uvicorn, "run", run)

    entry_point.main()

    assert started == [
        (
            "opscp_agent.main:create_app",
            {
                "factory": True,
                "host": "0.0.0.0",  # noqa: S104 -- the value under test
                "port": 8001,
                "log_config": None,
                "access_log": False,
                "ws": "none",
                "server_header": False,
            },
        )
    ]

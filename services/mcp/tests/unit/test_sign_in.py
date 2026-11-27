"""Who the server lets in over HTTP, and what each person is then shown."""

import base64
import hashlib
import hmac
import io
import json
import logging
import time
from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any
from uuid import UUID

import pytest
import structlog
from fastapi.testclient import TestClient
from fastmcp.server.auth.providers.jwt import JWTVerifier
from pydantic import AnyHttpUrl
from sqlalchemy.ext.asyncio import create_async_engine

from opscp_common.limits import Limits
from opscp_mcp.authz.identity import Identity
from opscp_mcp.authz.tokens import SCOPE, TokenIdentities, sign_in_with
from opscp_mcp.container import Services
from opscp_mcp.main import MCP_PATH, build_app
from opscp_mcp.settings import EntraSignIn
from opscp_mcp_testing.identities import ARJUN, KAVITA, PRIYA, ROHAN, SAM
from opscp_mcp_testing.server import fake_services
from opscp_mcp_testing.signin import (
    AUDIENCE,
    ISSUER,
    KEY_ID,
    PUBLIC_URL,
    bearer,
    claims_of,
    dev_sign_in,
    other_key,
    public_pem,
    signed,
    signing_key,
    token_for,
)
from opscp_mcp_testing.world import ASHA, STUCK_ON_ADDRESS

NOWHERE = "postgresql+asyncpg://nobody:unused@127.0.0.1:1/nothing"
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}
METADATA_PATH = "/.well-known/oauth-protected-resource/mcp"
AGENT_AUDIENCE = "api://opscp-agent"
READ_TOOLS = [
    "lookup_customer",
    "get_order",
    "list_tickets",
    "get_ticket",
    "search_runbooks",
    "get_policy_document",
]
HEALTH = "meridian://server/health"
L1_TOOLS = [*READ_TOOLS, "update_ticket"]
L2_TOOLS = [*L1_TOOLS, "issue_refund"]
READ_RESOURCES = [
    "meridian://reference/order-lifecycle",
    "meridian://policy/refund/summary",
    "meridian://runbooks/index",
    HEALTH,
]
READ_PROMPTS = ["investigate_order", "draft_customer_reply"]
TENANT = UUID("7f1c2d3e-0000-4000-8000-0000000000bb")
CLIENT = UUID("7f1c2d3e-0000-4000-8000-0000000000cc")
ENTRA_ISSUER = f"https://login.microsoftonline.com/{TENANT}/v2.0"


def _app(limits: Limits, sign_in: Any) -> Any:
    services = replace(fake_services(PRIYA, limits), identities=TokenIdentities())

    @asynccontextmanager
    async def open_services() -> AsyncGenerator[Services]:
        yield services

    return build_app(create_async_engine(NOWHERE), open_services, limits, sign_in)


@pytest.fixture
def http(limits: Limits) -> Iterator[TestClient]:
    with TestClient(_app(limits, dev_sign_in())) as client:
        yield client


def _post(http: TestClient, token: str | None, method: str = "tools/list", **params: Any) -> Any:
    headers = MCP_HEADERS | ({} if token is None else bearer(token))
    return http.post(
        MCP_PATH,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        headers=headers,
    )


def _result(http: TestClient, caller: Identity | str, method: str, **params: Any) -> dict[str, Any]:
    """The answer to a request made by a person, or with exactly the token given."""
    token = caller if isinstance(caller, str) else token_for(caller)
    response = _post(http, token, method, **params)
    assert response.status_code == 200, response.text
    (data,) = [line for line in response.text.splitlines() if line.startswith("data: ")]
    return json.loads(data.removeprefix("data: "))["result"]


def _called(
    http: TestClient, caller: Identity | str, tool: str, **arguments: Any
) -> dict[str, Any]:
    return _result(http, caller, "tools/call", name=tool, arguments=arguments)


def _error_code(result: dict[str, Any]) -> str:
    assert result["isError"] is True
    (block,) = result["content"]
    return json.loads(block["text"])["code"]


def test_a_request_without_a_token_is_told_where_to_learn_how_to_sign_in(
    http: TestClient,
) -> None:
    response = _post(http, None)

    challenge = response.headers["www-authenticate"]
    assert response.status_code == 401
    assert challenge.startswith("Bearer ")
    assert f'resource_metadata="{PUBLIC_URL}{METADATA_PATH}"' in challenge


def test_the_server_publishes_who_issues_its_tokens_without_sign_in(http: TestClient) -> None:
    metadata = http.get(METADATA_PATH)

    assert metadata.status_code == 200
    assert metadata.json() == {
        "resource": f"{PUBLIC_URL}/mcp",
        "authorization_servers": [ISSUER],
        "scopes_supported": [SCOPE],
        "bearer_methods_supported": ["header"],
        "resource_name": "Meridian Ops Copilot MCP server",
    }


def _segment(value: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()


def _unsigned() -> str:
    return f"{_segment({'alg': 'none', 'typ': 'JWT'})}.{_segment(claims_of(PRIYA))}."


def _signed_with_the_public_key_as_a_shared_secret() -> str:
    signing_input = f"{_segment({'alg': 'HS256', 'typ': 'JWT'})}.{_segment(claims_of(PRIYA))}"
    digest = hmac.new(public_pem().encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{base64.urlsafe_b64encode(digest).rstrip(b'=').decode()}"


def _tampered() -> str:
    header, _, signature = token_for(PRIYA).split(".")
    _, payload, _ = token_for(SAM).split(".")
    return f"{header}.{payload}.{signature}"


REFUSED: dict[str, Any] = {
    "issued for the agent, not for this server": lambda: token_for(PRIYA, aud=AGENT_AUDIENCE),
    "no audience": lambda: token_for(PRIYA, aud=None),
    "another issuer": lambda: token_for(PRIYA, iss="http://someone-else.localhost/"),
    "no issuer": lambda: token_for(PRIYA, iss=None),
    "expired": lambda: token_for(PRIYA, exp=int(time.time()) - 1),
    "never expires": lambda: token_for(PRIYA, exp=None),
    "not valid yet": lambda: token_for(PRIYA, nbf=int(time.time()) + 3600),
    "signed with another key": lambda: signed(claims_of(PRIYA), other_key()),
    "not signed": _unsigned,
    "signed with the public key as a secret": _signed_with_the_public_key_as_a_shared_secret,
    "payload swapped after signing": _tampered,
    "no scope": lambda: token_for(PRIYA, scp=None),
    "another scope": lambda: token_for(PRIYA, scp="Agent.Access"),
    "no object id": lambda: token_for(PRIYA, oid=None),
    "object id that is not one": lambda: token_for(PRIYA, oid="priya"),
    "no name": lambda: token_for(PRIYA, name=None),
    "empty name": lambda: token_for(PRIYA, name=""),
    "a region that does not exist": lambda: token_for(PRIYA, region="north"),
    "roles that are not a list": lambda: token_for(PRIYA, roles="Support.Supervisor"),
    "every region, for someone who is not a supervisor": lambda: token_for(PRIYA, region="*"),
    "not a token": lambda: "not-a-token",
    "empty": lambda: "",
}


@pytest.mark.parametrize("token", REFUSED.values(), ids=REFUSED.keys())
def test_a_token_that_does_not_prove_a_caller_is_refused(http: TestClient, token: Any) -> None:
    response = _post(http, token())

    assert response.status_code == 401
    assert "www-authenticate" in response.headers


def test_only_the_bearer_scheme_is_accepted(http: TestClient) -> None:
    response = http.post(
        MCP_PATH,
        json={},
        headers=MCP_HEADERS | {"Authorization": f"Basic {token_for(PRIYA)}"},
    )

    assert response.status_code == 401


def test_a_token_in_the_address_is_not_read(http: TestClient) -> None:
    response = http.post(
        f"{MCP_PATH}?access_token={token_for(PRIYA)}", json={}, headers=MCP_HEADERS
    )

    assert response.status_code == 401


SHOWN: dict[str, tuple[Identity, list[str], list[str], list[str]]] = {
    "an L1 engineer": (PRIYA, L1_TOOLS, READ_RESOURCES, READ_PROMPTS),
    "an L1 engineer in the south": (ROHAN, L1_TOOLS, READ_RESOURCES, READ_PROMPTS),
    "an L2 engineer": (ARJUN, L2_TOOLS, READ_RESOURCES, READ_PROMPTS),
    "a supervisor": (KAVITA, L2_TOOLS, READ_RESOURCES, READ_PROMPTS),
    "an administrator": (SAM, [], [HEALTH], []),
}


@pytest.mark.parametrize(
    ("identity", "tools", "resources", "prompts"), SHOWN.values(), ids=SHOWN.keys()
)
def test_each_role_is_shown_what_it_may_use(
    http: TestClient,
    identity: Identity,
    tools: list[str],
    resources: list[str],
    prompts: list[str],
) -> None:
    listed_tools = _result(http, identity, "tools/list")["tools"]
    listed_resources = _result(http, identity, "resources/list")["resources"]
    listed_prompts = _result(http, identity, "prompts/list")["prompts"]

    assert [tool["name"] for tool in listed_tools] == tools
    assert [resource["uri"] for resource in listed_resources] == resources
    assert [prompt["name"] for prompt in listed_prompts] == prompts


def test_what_is_hidden_from_a_caller_is_also_refused_when_called(http: TestClient) -> None:
    called = _called(http, SAM, "get_order", order_id=STUCK_ON_ADDRESS)

    assert _error_code(called) == "PERMISSION_DENIED"


def test_a_signed_in_person_with_no_role_is_shown_nothing_and_can_call_nothing(
    http: TestClient,
) -> None:
    nobody = token_for(PRIYA, roles=[])

    listed = _result(http, nobody, "tools/list")
    called = _result(
        http, nobody, "tools/call", name="get_order", arguments={"order_id": STUCK_ON_ADDRESS}
    )

    assert listed["tools"] == []
    assert _error_code(called) == "PERMISSION_DENIED"


def test_a_role_this_server_does_not_define_grants_nothing(http: TestClient) -> None:
    listed = _result(
        http, token_for(PRIYA, roles=["Global.Administrator", "support.l2"]), "tools/list"
    )

    assert listed["tools"] == []


def test_the_caller_is_whoever_the_token_names(http: TestClient) -> None:
    as_l1 = _called(http, PRIYA, "lookup_customer", customer_id=ASHA)
    as_l2 = _called(http, ARJUN, "lookup_customer", customer_id=ASHA)
    from_another_region = _called(http, ROHAN, "lookup_customer", customer_id=ASHA)

    assert as_l1["structuredContent"]["data"]["email"] == "a***@example.in"
    assert as_l2["structuredContent"]["data"]["email"] == "asha.rao@example.in"
    assert _error_code(from_another_region) == "NOT_FOUND"


def test_an_engineer_whose_token_names_no_region_reads_no_customer(http: TestClient) -> None:
    called = _result(
        http,
        token_for(PRIYA, region=None),
        "tools/call",
        name="lookup_customer",
        arguments={"customer_id": ASHA},
    )

    assert _error_code(called) == "PERMISSION_DENIED"


@pytest.mark.parametrize("host", ["mcp.test", "127.0.0.1:8000", "localhost:8000"])
@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "http://mcp.test",
        "http://127.0.0.1:8000",
        "http://localhost:3000",
        "null",
    ],
)
def test_a_request_from_a_web_page_is_refused_even_with_a_good_token(
    http: TestClient, origin: str, host: str
) -> None:
    response = http.post(
        MCP_PATH,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers=MCP_HEADERS | bearer(token_for(PRIYA)) | {"Origin": origin, "Host": host},
    )

    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers


def test_a_web_page_cannot_ask_permission_to_send_a_token(http: TestClient) -> None:
    preflight = http.options(
        MCP_PATH,
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization",
        },
    )

    assert preflight.status_code == 403


@pytest.fixture
def server_log() -> Iterator[io.StringIO]:
    written = io.StringIO()
    handler = logging.StreamHandler(written)
    root = logging.getLogger()
    before = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    yield written
    root.removeHandler(handler)
    root.setLevel(before)


def test_a_token_is_never_written_to_the_log(http: TestClient, server_log: io.StringIO) -> None:
    accepted, refused = token_for(PRIYA), token_for(PRIYA, aud=AGENT_AUDIENCE)

    with structlog.testing.capture_logs() as captured:
        _post(http, accepted)
        _post(http, refused)
        _post(http, token_for(PRIYA, oid="priya"))

    logged = server_log.getvalue() + json.dumps(captured, default=str)
    for token in (accepted, refused):
        assert token not in logged
        assert token.split(".")[2] not in logged
    assert {"event": "token_refused", "claims": ["oid"]}.items() <= captured[-1].items()


@pytest.fixture
def entra(limits: Limits, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Entra sign-in, with the tenant's published keys replaced by the test key."""

    async def published_keys(_self: JWTVerifier) -> dict[str, Any]:
        return {"keys": [signing_key().as_dict(private=False)]}

    monkeypatch.setattr(JWTVerifier, "_fetch_jwks", published_keys)
    sign_in = sign_in_with(
        EntraSignIn(tenant_id=TENANT, client_id=CLIENT, audience=AUDIENCE),
        AnyHttpUrl(PUBLIC_URL),
    )
    with TestClient(_app(limits, sign_in)) as client:
        yield client


def _entra_token(**changed: Any) -> str:
    return token_for(
        ARJUN, **({"iss": ENTRA_ISSUER, "aud": str(CLIENT), "tid": str(TENANT)} | changed)
    )


def test_a_token_from_the_tenant_for_this_application_is_accepted(entra: TestClient) -> None:
    by_client_id = _result(entra, _entra_token(), "tools/list")
    by_application_uri = _result(entra, _entra_token(aud=AUDIENCE), "tools/list")

    assert [tool["name"] for tool in by_client_id["tools"]] == L2_TOOLS
    assert by_application_uri == by_client_id


ENTRA_REFUSED: dict[str, Any] = {
    "another tenant": lambda: _entra_token(
        iss="https://login.microsoftonline.com/7f1c2d3e-0000-4000-8000-0000000000dd/v2.0"
    ),
    "the version 1 issuer": lambda: _entra_token(iss=f"https://sts.windows.net/{TENANT}/"),
    "the local issuer": lambda: token_for(ARJUN),
    "another application": lambda: _entra_token(aud="7f1c2d3e-0000-4000-8000-0000000000ee"),
    "the agent": lambda: _entra_token(aud=AGENT_AUDIENCE),
    "a key the tenant does not publish": lambda: signed(
        claims_of(ARJUN, iss=ENTRA_ISSUER, aud=str(CLIENT)), other_key(), kid="unpublished"
    ),
    "a published key id with another key": lambda: signed(
        claims_of(ARJUN, iss=ENTRA_ISSUER, aud=str(CLIENT)), other_key(), kid=KEY_ID
    ),
    "no scope": lambda: _entra_token(scp=None),
}


@pytest.mark.parametrize("token", ENTRA_REFUSED.values(), ids=ENTRA_REFUSED.keys())
def test_entra_sign_in_refuses_a_token_from_anywhere_else(entra: TestClient, token: Any) -> None:
    assert _post(entra, token()).status_code == 401


def test_entra_sign_in_names_the_tenant_and_the_scope_to_ask_for(entra: TestClient) -> None:
    metadata = entra.get(METADATA_PATH).json()

    assert metadata["authorization_servers"] == [ENTRA_ISSUER]
    assert metadata["scopes_supported"] == [f"{AUDIENCE}/{SCOPE}"]

"""The local issuer: what it exchanges, and everything it refuses."""

import base64
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import RSAKey
from pydantic import ValidationError

from opscp_dev_issuer.app import TOKEN_PATH, build_app, create_app
from opscp_dev_issuer.exchange import DEMO_GRANT, GRANT_TYPE, Client, Exchange, SignInClient
from opscp_dev_issuer.settings import IssuerSettings

ISSUER = "http://dev-issuer.localhost/"
AGENT = "api://opscp-agent"
MCP = "api://opscp-mcp"
SECRET = "a-client-secret-of-thirty-two-chars!"  # noqa: S105 -- made for the test
WEB = "opscp-web"
WEB_SECRET = "the-web-client-secret-for-a-test!!"  # noqa: S105 -- made for the test
NOW = 1_800_000_000
KEY = RSAKey.generate_key(2048, private=True)
OTHER_KEY = RSAKey.generate_key(2048, private=True)
PERSON = {
    "iss": ISSUER,
    "aud": AGENT,
    "iat": NOW,
    "exp": NOW + 600,
    "sub": "0f1e2d3c-0001-4000-8000-000000000001",
    "oid": "0f1e2d3c-0001-4000-8000-000000000001",
    "name": "Priya Nair",
    "roles": ["Support.L1"],
    "region": "west",
    "scp": "Agent.Access",
    "amr": ["pwd"],
}


def _signed(claims: dict[str, Any], key: RSAKey = KEY, alg: str = "RS256") -> str:
    return jwt.encode({"alg": alg}, claims, key)


def _form(**changed: Any) -> dict[str, str]:
    form = {
        "grant_type": GRANT_TYPE,
        "requested_token_use": "on_behalf_of",
        "client_id": "opscp-agent-api",
        "client_secret": SECRET,
        "scope": f"{MCP}/.default",
        "assertion": _signed(PERSON),
    } | changed
    return {name: value for name, value in form.items() if value is not None}


@pytest.fixture
def http() -> TestClient:
    client = Client(
        client_id="opscp-agent-api", secret=SECRET, audience=AGENT, downstream_audience=MCP
    )
    exchange = Exchange(
        ISSUER,
        KEY.as_pem(private=True).decode("ascii"),
        client,
        now=lambda: float(NOW),
        sign_in=SignInClient(client_id=WEB, secret=WEB_SECRET, audience=AGENT),
    )
    return TestClient(build_app(exchange))


def _sign_in(**changed: Any) -> dict[str, str]:
    form = {
        "grant_type": DEMO_GRANT,
        "client_id": WEB,
        "client_secret": WEB_SECRET,
        "scope": f"{AGENT}/Agent.Access",
        "user": "priya",
    } | changed
    return {name: value for name, value in form.items() if value is not None}


def test_a_person_s_token_is_exchanged_for_one_that_names_them_to_the_mcp_server(
    http: TestClient,
) -> None:
    response = http.post(TOKEN_PATH, data=_form())

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert (body["token_type"], body["expires_in"], body["scope"]) == (
        "Bearer",
        3600,
        f"{MCP}/.default",
    )
    claims = jwt.decode(body["access_token"], KEY).claims
    assert claims == {
        "iss": ISSUER,
        "aud": MCP,
        "iat": NOW,
        "nbf": NOW,
        "exp": NOW + 3600,
        "azp": "opscp-agent-api",
        "scp": "Mcp.Access",
        "sub": PERSON["sub"],
        "oid": PERSON["oid"],
        "name": "Priya Nair",
        "roles": ["Support.L1"],
        "region": "west",
    }


def test_a_person_with_no_region_gets_a_token_with_none(http: TestClient) -> None:
    admin = {name: value for name, value in PERSON.items() if name != "region"}

    body = http.post(TOKEN_PATH, data=_form(assertion=_signed(admin))).json()

    assert "region" not in jwt.decode(body["access_token"], KEY).claims


def test_the_named_scope_may_be_asked_for_in_place_of_the_default(http: TestClient) -> None:
    assert http.post(TOKEN_PATH, data=_form(scope=f"{MCP}/Mcp.Access")).status_code == 200


@pytest.mark.parametrize(
    ("changed", "status", "error"),
    [
        ({"grant_type": "client_credentials"}, 400, "unsupported_grant_type"),
        ({"grant_type": None}, 400, "unsupported_grant_type"),
        ({"requested_token_use": None}, 400, "invalid_request"),
        ({"client_secret": "wrong-secret"}, 401, "invalid_client"),
        ({"client_secret": None}, 401, "invalid_client"),
        ({"client_id": "someone-else"}, 401, "invalid_client"),
        ({"scope": "api://opscp-payments/.default"}, 400, "invalid_scope"),
        ({"scope": f"{AGENT}/.default"}, 400, "invalid_scope"),
        ({"scope": None}, 400, "invalid_scope"),
        ({"assertion": None}, 400, "invalid_grant"),
        ({"assertion": "not.a.token"}, 400, "invalid_grant"),
        ({"assertion": _signed(PERSON, OTHER_KEY)}, 400, "invalid_grant"),
        ({"assertion": _signed(PERSON | {"exp": NOW - 1})}, 400, "invalid_grant"),
        ({"assertion": _signed(PERSON | {"aud": MCP})}, 400, "invalid_grant"),
        ({"assertion": _signed(PERSON | {"iss": "http://elsewhere/"})}, 400, "invalid_grant"),
        ({"assertion": _signed(PERSON | {"scp": "Mcp.Access"})}, 400, "invalid_grant"),
        (
            {"assertion": _signed({k: v for k, v in PERSON.items() if k != "oid"})},
            400,
            "invalid_grant",
        ),
        (
            {"assertion": _signed({k: v for k, v in PERSON.items() if k != "exp"})},
            400,
            "invalid_grant",
        ),
    ],
)
def test_anything_else_is_refused_in_the_words_of_the_standard(
    http: TestClient, changed: dict[str, Any], status: int, error: str
) -> None:
    response = http.post(TOKEN_PATH, data=_form(**changed))

    assert (response.status_code, response.json()["error"]) == (status, error)
    assert "access_token" not in response.json()


def test_a_token_issued_for_the_mcp_server_cannot_be_exchanged_again(http: TestClient) -> None:
    issued = http.post(TOKEN_PATH, data=_form()).json()["access_token"]

    again = http.post(TOKEN_PATH, data=_form(assertion=issued))

    assert (again.status_code, again.json()["error"]) == (400, "invalid_grant")


def test_a_body_that_is_not_a_form_or_is_too_large_is_refused(http: TestClient) -> None:
    as_json = http.post(TOKEN_PATH, json=_form())
    too_large = http.post(TOKEN_PATH, data=_form(assertion="x" * 20_000))

    assert (as_json.status_code, as_json.json()["error"]) == (400, "invalid_request")
    assert too_large.status_code == 413


def test_the_probe_answers_and_nothing_describes_the_service(http: TestClient) -> None:
    assert http.get("/healthz").json() == {"status": "ok"}
    assert [http.get(path).status_code for path in ("/docs", "/openapi.json")] == [404, 404]


ENVIRONMENT = {
    "ENVIRONMENT": "local",
    "DEV_ISSUER": ISSUER,
    "DEV_ISSUER_PRIVATE_KEY": base64.b64encode(KEY.as_pem(private=True)).decode(),
    "AGENT_API_AUDIENCE": AGENT,
    "AGENT_API_CLIENT_ID": "opscp-agent-api",
    "AGENT_CLIENT_SECRET": SECRET,
    "MCP_API_AUDIENCE": MCP,
    "WEB_CLIENT_ID": WEB,
    "WEB_CLIENT_SECRET": WEB_SECRET,
}


def _set(monkeypatch: pytest.MonkeyPatch, **changed: str) -> None:
    for name, value in (ENVIRONMENT | changed).items():
        monkeypatch.setenv(name, value)


def test_the_issuer_starts_from_the_environment_on_a_laptop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set(monkeypatch)
    person = PERSON | {"iat": int(time.time()), "exp": int(time.time()) + 600}

    with TestClient(create_app()) as http:
        response = http.post(TOKEN_PATH, data=_form(assertion=_signed(person)))

    assert response.status_code == 200


@pytest.mark.parametrize(
    ("changed", "message"),
    [
        ({"ENVIRONMENT": "demo"}, "environment"),
        ({"ENVIRONMENT": "prod"}, "environment"),
        ({"AGENT_CLIENT_SECRET": "short"}, "agent_client_secret"),
        ({"WEB_CLIENT_SECRET": "short"}, "web_client_secret"),
        ({"WEB_CLIENT_ID": ""}, "web_client_id"),
        ({"DEV_ISSUER_PRIVATE_KEY": "not base64!"}, "base64-encoded PEM RSA private key"),
        (
            {"DEV_ISSUER_PRIVATE_KEY": base64.b64encode(KEY.as_pem(private=False)).decode()},
            "base64-encoded PEM RSA private key",
        ),
        ({"DEV_ISSUER": "dev-issuer"}, "dev_issuer"),
    ],
)
def test_the_issuer_refuses_to_start_anywhere_but_a_laptop_or_without_its_key(
    monkeypatch: pytest.MonkeyPatch, changed: dict[str, str], message: str
) -> None:
    _set(monkeypatch, **changed)

    with pytest.raises(ValidationError, match=message) as refused:
        IssuerSettings()  # pyright: ignore[reportCallIssue] -- fields come from the environment

    assert SECRET not in str(refused.value)
    assert WEB_SECRET not in str(refused.value)


@pytest.mark.parametrize(
    ("user", "name", "roles", "region"),
    [
        ("priya", "Priya Nair", ["Support.L1"], "west"),
        ("arjun", "Arjun Mehta", ["Support.L2"], "west"),
        ("kavita", "Kavita Rao", ["Support.Supervisor"], "*"),
        ("sam", "Sam Admin", ["Platform.Admin"], None),
    ],
)
def test_the_web_application_signs_a_demo_user_in_for_the_agent(
    http: TestClient, user: str, name: str, roles: list[str], region: str | None
) -> None:
    response = http.post(TOKEN_PATH, data=_sign_in(user=user))

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert (body["token_type"], body["expires_in"]) == ("Bearer", 3600)
    claims = jwt.decode(body["access_token"], KEY, algorithms=["RS256"]).claims
    assert (claims["iss"], claims["aud"], claims["azp"]) == (ISSUER, AGENT, WEB)
    assert (claims["name"], claims["roles"], claims.get("region")) == (name, roles, region)
    assert (claims["scp"], claims["sub"]) == ("Agent.Access", claims["oid"])
    assert (claims["iat"], claims["exp"]) == (NOW, NOW + 3600)


def test_a_demo_user_s_token_can_be_exchanged_like_any_person_s(http: TestClient) -> None:
    signed_in = http.post(TOKEN_PATH, data=_sign_in()).json()["access_token"]

    exchanged = http.post(TOKEN_PATH, data=_form(assertion=signed_in))

    assert exchanged.status_code == 200
    claims = jwt.decode(exchanged.json()["access_token"], KEY, algorithms=["RS256"]).claims
    assert (claims["aud"], claims["name"]) == (MCP, "Priya Nair")


@pytest.mark.parametrize(
    ("changed", "status", "error"),
    [
        ({"user": "mallory"}, 400, "invalid_grant"),
        ({"user": None}, 400, "invalid_grant"),
        ({"client_secret": "x" * 32}, 401, "invalid_client"),
        ({"client_secret": None}, 401, "invalid_client"),
        ({"client_id": "opscp-agent-api", "client_secret": SECRET}, 401, "invalid_client"),
        ({"scope": f"{MCP}/.default"}, 400, "invalid_scope"),
        ({"scope": None}, 400, "invalid_scope"),
    ],
)
def test_a_demo_sign_in_that_is_not_the_web_application_s_is_refused(
    http: TestClient, changed: dict[str, Any], status: int, error: str
) -> None:
    response = http.post(TOKEN_PATH, data=_sign_in(**changed))

    assert (response.status_code, response.json()["error"]) == (status, error)
    assert "access_token" not in response.json()


def test_the_agent_s_own_client_cannot_sign_a_demo_user_in_for_the_mcp_server(
    http: TestClient,
) -> None:
    response = http.post(
        TOKEN_PATH,
        data=_sign_in(client_id="opscp-agent-api", client_secret=SECRET, scope=f"{MCP}/Mcp.Access"),
    )

    assert response.status_code == 401


def test_an_issuer_without_a_sign_in_client_offers_no_demo_sign_in() -> None:
    client = Client(
        client_id="opscp-agent-api", secret=SECRET, audience=AGENT, downstream_audience=MCP
    )
    exchange = Exchange(ISSUER, KEY.as_pem(private=True).decode("ascii"), client)

    response = TestClient(build_app(exchange)).post(TOKEN_PATH, data=_sign_in())

    assert (response.status_code, response.json()["error"]) == (400, "unsupported_grant_type")

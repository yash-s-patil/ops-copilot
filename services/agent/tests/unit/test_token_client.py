"""The token client: the exchange itself, and how long an exchanged token is kept."""

import asyncio
from typing import Any
from urllib.parse import parse_qs

import httpx2
import pytest
from azure.core.credentials import AccessToken as AzureToken
from azure.core.exceptions import ClientAuthenticationError

from opscp_agent.auth.obo import (
    ASSERTION_TYPE,
    FEDERATION_SCOPE,
    GRANT_TYPE,
    AccessToken,
    ExchangeTarget,
    FederatedProof,
    OnBehalfOf,
    ReauthRequiredError,
    SecretProof,
    TokenClient,
    TokenServiceError,
)

TARGET = ExchangeTarget(
    token_url="https://login.test/tenant/oauth2/v2.0/token",  # noqa: S106 -- an address
    client_id="agent-client",
    scope="api://opscp-mcp/.default",
    timeout_s=5,
)
NOW = 1_000_000.0


def _exchange(handler: Any, proof: Any = None) -> OnBehalfOf:
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return OnBehalfOf(http, TARGET, proof or SecretProof("s3cret"), clock=lambda: NOW)


async def test_the_exchange_is_the_on_behalf_of_request_entra_defines() -> None:
    sent: list[httpx2.Request] = []

    def issue(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return httpx2.Response(200, json={"access_token": "token-b", "expires_in": 3600})

    issued = await _exchange(issue).exchange("token-a")

    assert (issued.value, issued.expires_at) == ("token-b", NOW + 3600)
    (request,) = sent
    assert str(request.url) == TARGET.token_url
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    assert parse_qs(request.content.decode()) == {
        "grant_type": [GRANT_TYPE],
        "client_id": ["agent-client"],
        "client_secret": ["s3cret"],
        "assertion": ["token-a"],
        "scope": ["api://opscp-mcp/.default"],
        "requested_token_use": ["on_behalf_of"],
    }


@pytest.mark.parametrize("error", ["invalid_grant", "interaction_required"])
async def test_a_person_s_token_the_issuer_no_longer_accepts_means_sign_in_again(
    error: str,
) -> None:
    def refuse(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(400, json={"error": error, "error_description": "AADSTS500133"})

    with pytest.raises(ReauthRequiredError):
        await _exchange(refuse).exchange("token-a")


@pytest.mark.parametrize(
    "answer",
    [
        httpx2.Response(401, json={"error": "invalid_client"}),
        httpx2.Response(500, text="oops"),
        httpx2.Response(400, text="not json"),
        httpx2.Response(200, json={"access_token": "", "expires_in": 3600}),
        httpx2.Response(200, json={"access_token": "t", "expires_in": 0}),
        httpx2.Response(200, text="<html>"),
    ],
)
async def test_any_other_answer_is_a_failure_of_the_sign_in_service(
    answer: httpx2.Response,
) -> None:
    def answered(_request: httpx2.Request) -> httpx2.Response:
        return answer

    with pytest.raises(TokenServiceError):
        await _exchange(answered).exchange("token-a")


async def test_an_issuer_that_cannot_be_reached_is_a_failure_of_the_sign_in_service() -> None:
    def unreachable(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectTimeout("timed out", request=request)

    with pytest.raises(TokenServiceError):
        await _exchange(unreachable).exchange("token-a")


class _Identity:
    def __init__(self, *, fails: bool = False) -> None:
        self.asked: list[str] = []
        self._fails = fails

    async def get_token(self, *scopes: str, **_kwargs: Any) -> AzureToken:
        self.asked += scopes
        if self._fails:
            raise ClientAuthenticationError("no identity")
        return AzureToken("identity-token", int(NOW) + 600)


async def test_in_azure_the_service_proves_itself_with_its_managed_identity() -> None:
    identity = _Identity()
    sent: list[dict[str, list[str]]] = []

    def issue(request: httpx2.Request) -> httpx2.Response:
        sent.append(parse_qs(request.content.decode()))
        return httpx2.Response(200, json={"access_token": "token-b", "expires_in": 60})

    await _exchange(issue, FederatedProof(identity)).exchange("token-a")  # pyright: ignore[reportArgumentType] -- a stand-in

    assert identity.asked == [FEDERATION_SCOPE]
    assert sent[0]["client_assertion"] == ["identity-token"]
    assert sent[0]["client_assertion_type"] == [ASSERTION_TYPE]
    assert "client_secret" not in sent[0]


async def test_without_a_managed_identity_token_nothing_is_asked_of_the_issuer() -> None:
    asked: list[httpx2.Request] = []

    def issue(request: httpx2.Request) -> httpx2.Response:
        asked.append(request)
        return httpx2.Response(200, json={})

    with pytest.raises(TokenServiceError):
        await _exchange(issue, FederatedProof(_Identity(fails=True))).exchange("token-a")  # pyright: ignore[reportArgumentType] -- a stand-in
    assert asked == []


class _Issuer:
    def __init__(self, lifetime_s: float = 3600) -> None:
        self.asked: list[str] = []
        self.now = NOW
        self._lifetime_s = lifetime_s

    async def exchange(self, assertion: str) -> AccessToken:
        self.asked.append(assertion)
        await asyncio.sleep(0)
        return AccessToken(f"b{len(self.asked)}-for-{assertion}", self.now + self._lifetime_s)


def _client(issuer: _Issuer, kept: int = 8) -> TokenClient:
    return TokenClient(issuer, refresh_before_s=300, kept=kept, clock=lambda: issuer.now)


async def test_an_exchanged_token_is_reused_until_shortly_before_it_expires() -> None:
    issuer = _Issuer()
    tokens = _client(issuer)

    first = await tokens.token_for("a")
    issuer.now += 3600 - 301
    assert await tokens.token_for("a") == first
    issuer.now += 2
    renewed = await tokens.token_for("a")

    assert (first, renewed) == ("b1-for-a", "b2-for-a")
    assert issuer.asked == ["a", "a"]


async def test_each_person_s_token_is_exchanged_and_kept_apart() -> None:
    issuer = _Issuer()
    tokens = _client(issuer)

    assert await tokens.token_for("priya") == "b1-for-priya"
    assert await tokens.token_for("arjun") == "b2-for-arjun"
    assert await tokens.token_for("priya") == "b1-for-priya"


async def test_a_token_the_server_refused_is_replaced_once_however_many_ask() -> None:
    issuer = _Issuer()
    tokens = _client(issuer)
    refused = await tokens.token_for("a")

    replaced = await asyncio.gather(*(tokens.token_for("a", replacing=refused) for _ in range(5)))

    assert set(replaced) == {"b2-for-a"}
    assert issuer.asked == ["a", "a"]


async def test_callers_who_arrive_together_share_one_exchange() -> None:
    issuer = _Issuer()
    tokens = _client(issuer)

    given = await asyncio.gather(*(tokens.token_for("a") for _ in range(10)))

    assert set(given) == {"b1-for-a"}
    assert issuer.asked == ["a"]


async def test_only_so_many_tokens_are_kept_and_the_least_used_go_first() -> None:
    issuer = _Issuer()
    tokens = _client(issuer, kept=2)

    await tokens.token_for("a")
    await tokens.token_for("b")
    await tokens.token_for("a")
    await tokens.token_for("c")
    await tokens.token_for("a")
    await tokens.token_for("b")

    assert issuer.asked == ["a", "b", "c", "b"]


async def test_a_failed_exchange_is_raised_and_nothing_is_kept() -> None:
    class Refusing:
        async def exchange(self, assertion: str) -> AccessToken:
            raise ReauthRequiredError

    tokens = TokenClient(Refusing(), refresh_before_s=300, kept=2)

    for _ in range(2):
        with pytest.raises(ReauthRequiredError):
            await tokens.token_for("a")


def test_a_token_is_never_shown_when_its_holder_is_printed() -> None:
    assert "secret-value" not in repr(AccessToken("secret-value", NOW))

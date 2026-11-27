"""Which tokens the simulated payment service accepts as the worker's."""

import base64
import hashlib
import hmac
import json
from typing import Any
from uuid import UUID

import httpx2
import pytest
from joserfc.jwk import RSAKey

from opscp_common.limits import SimLimits
from opscp_payments.settings import DevCallers, EntraCallers
from opscp_payments.sim.auth import (
    CallerTokens,
    FixedKey,
    KeysUnavailableError,
    PublishedKeys,
    caller_tokens,
)
from opscp_payments_testing.keys import (
    AUDIENCE,
    ISSUER,
    NOW,
    issuer_key,
    public_pem,
    signed,
    worker_claims,
)

TENANT = UUID("7d0c2f4e-1111-4222-8333-444455556666")
ENTRA_ISSUER = f"https://login.microsoftonline.com/{TENANT}/v2.0"
KEYS_URL = f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys"
CLIENT_ID = "0a1b2c3d-aaaa-4bbb-8ccc-ddddeeeeffff"
LIMITS = SimLimits(
    settle_delay_s=3,
    refunds_kept=10000,
    keys_timeout_s=5,
    keys_refresh_s=3600,
    keys_renew_min_s=300,
    keys_retry_s=10,
    keys_keep_s=86400,
    clock_skew_s=60,
)


def _dev() -> CallerTokens:
    return CallerTokens(
        FixedKey(public_pem()),
        issuer=ISSUER,
        audience=AUDIENCE,
        tenant_id=None,
        clock_skew_s=60,
        now=lambda: NOW,
    )


async def test_the_workers_token_is_accepted() -> None:
    assert await _dev().verdict(signed(worker_claims())) == "accepted"


async def test_an_audience_given_as_a_list_may_name_this_service() -> None:
    token = signed(worker_claims(aud=["api://other", AUDIENCE]))

    assert await _dev().verdict(token) == "accepted"


@pytest.mark.parametrize(
    "changed",
    [
        {"iss": "http://someone-else.localhost/"},
        {"aud": "api://opscp-mcp"},
        {"aud": ["api://opscp-mcp"]},
        {"aud": None},
        {"exp": NOW},
        {"exp": NOW - 1},
        {"exp": None},
        {"exp": "1900000000"},
        {"exp": True},
        {"nbf": NOW + 61},
        {"nbf": "soon"},
    ],
    ids=lambda changed: ",".join(f"{k}={v}" for k, v in changed.items()),
)
async def test_a_token_not_meant_for_this_service_now_is_unknown(changed: dict[str, Any]) -> None:
    assert await _dev().verdict(signed(worker_claims(**changed))) == "unknown"


async def test_a_token_a_minute_early_is_allowed_for_clock_skew() -> None:
    assert await _dev().verdict(signed(worker_claims(nbf=NOW + 60))) == "accepted"


@pytest.mark.parametrize(
    "changed",
    [
        {"roles": None},
        {"roles": []},
        {"roles": ["Support.L2"]},
        {"roles": "Payments.Submit"},
        {"scp": "Mcp.Access"},
        {"oid": "0f1e2d3c-0003-4000-8000-000000000003"},
        {"oid": None},
        {"sub": None, "oid": None},
        {"idtyp": "user"},
    ],
    ids=[
        "no roles",
        "empty roles",
        "another role",
        "roles as text",
        "a delegated scope",
        "a subject that is not the caller",
        "no object id",
        "no subject",
        "marked as a person's",
    ],
)
async def test_a_trusted_token_that_is_not_the_workers_is_not_allowed(
    changed: dict[str, Any],
) -> None:
    assert await _dev().verdict(signed(worker_claims(**changed))) == "not_allowed"


async def test_a_token_signed_with_another_key_is_unknown() -> None:
    assert await _dev().verdict(signed(worker_claims(), kid="stranger")) == "unknown"


@pytest.mark.parametrize("token", ["", "not-a-token", "a.b.c", "eyJhbGciOiJub25lIn0.e30."])
async def test_text_that_is_not_a_signed_token_is_unknown(token: str) -> None:
    assert await _dev().verdict(token) == "unknown"


async def test_a_token_signed_with_the_public_key_as_a_shared_secret_is_unknown() -> None:
    """The classic confusion: an HS256 token keyed with the RSA public key everyone can read."""

    def encoded(part: bytes) -> bytes:
        return base64.urlsafe_b64encode(part).rstrip(b"=")

    head = encoded(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = encoded(json.dumps(worker_claims()).encode())
    seal = hmac.new(public_pem().encode("ascii"), head + b"." + body, hashlib.sha256).digest()
    forged = b".".join((head, body, encoded(seal))).decode("ascii")

    assert await _dev().verdict(forged) == "unknown"


class _Entra:
    """Microsoft's key endpoint, played by a transport the test controls."""

    def __init__(self) -> None:
        self.published = ["first"]
        self.fetches = 0
        self.down = False
        self.garbled = False
        self.seconds = 0.0
        http = httpx2.AsyncClient(transport=httpx2.MockTransport(self._answer))
        self.keys = PublishedKeys(http, KEYS_URL, LIMITS, now=lambda: self.seconds)
        self.callers = CallerTokens(
            self.keys,
            issuer=ENTRA_ISSUER,
            audience=CLIENT_ID,
            tenant_id=str(TENANT),
            clock_skew_s=LIMITS.clock_skew_s,
            now=lambda: NOW,
        )

    def _answer(self, request: httpx2.Request) -> httpx2.Response:
        assert str(request.url) == KEYS_URL
        self.fetches += 1
        if self.down:
            return httpx2.Response(503)
        if self.garbled:
            return httpx2.Response(200, json={"keys": "not a list"})
        keys = [issuer_key(kid).as_dict(private=False) for kid in self.published]
        return httpx2.Response(200, json={"keys": keys})

    def token(self, kid: str = "first", **changed: Any) -> str:
        claims = {"iss": ENTRA_ISSUER, "aud": CLIENT_ID, "tid": str(TENANT)} | changed
        return signed(worker_claims(**claims), kid=kid)


async def test_a_token_from_the_tenant_for_this_service_is_accepted() -> None:
    entra = _Entra()

    assert await entra.callers.verdict(entra.token()) == "accepted"
    assert await entra.callers.verdict(entra.token()) == "accepted"
    assert entra.fetches == 1


@pytest.mark.parametrize(
    "changed",
    [{"tid": "11111111-2222-4333-8444-555555555555"}, {"tid": None}, {"iss": ISSUER}],
    ids=["another tenant", "no tenant", "another issuer"],
)
async def test_a_token_from_elsewhere_is_unknown(changed: dict[str, Any]) -> None:
    entra = _Entra()

    assert await entra.callers.verdict(entra.token(**changed)) == "unknown"


async def test_keys_are_fetched_again_after_an_hour() -> None:
    entra = _Entra()
    await entra.callers.verdict(entra.token())
    entra.seconds = 3599
    await entra.callers.verdict(entra.token())
    still_one = entra.fetches
    entra.seconds = 3600

    await entra.callers.verdict(entra.token())

    assert (still_one, entra.fetches) == (1, 2)


async def test_a_token_naming_a_new_key_makes_the_keys_be_fetched_again() -> None:
    entra = _Entra()
    await entra.callers.verdict(entra.token())
    entra.published = ["first", "rolled"]
    entra.seconds = 300

    assert await entra.callers.verdict(entra.token(kid="rolled")) == "accepted"
    assert entra.fetches == 2


async def test_unknown_keys_cannot_make_the_service_fetch_more_than_every_five_minutes() -> None:
    entra = _Entra()
    await entra.callers.verdict(entra.token())
    entra.seconds = 299

    for _ in range(5):
        assert await entra.callers.verdict(entra.token(kid="stranger")) == "unknown"

    assert entra.fetches == 1


async def test_a_key_still_unknown_after_fetching_again_is_unknown() -> None:
    entra = _Entra()
    await entra.callers.verdict(entra.token())
    entra.seconds = 300

    assert await entra.callers.verdict(entra.token(kid="stranger")) == "unknown"
    assert entra.fetches == 2


@pytest.mark.parametrize("fault", ["down", "garbled"])
async def test_the_last_good_keys_are_kept_when_a_fetch_fails(fault: str) -> None:
    entra = _Entra()
    await entra.callers.verdict(entra.token())
    setattr(entra, fault, True)
    entra.seconds = 3600

    assert await entra.callers.verdict(entra.token()) == "accepted"
    assert entra.fetches == 2


async def test_with_no_keys_at_all_nothing_can_be_checked() -> None:
    entra = _Entra()
    entra.down = True

    with pytest.raises(KeysUnavailableError):
        await entra.callers.verdict(entra.token())


async def test_while_no_keys_are_held_they_are_asked_for_at_most_every_few_seconds() -> None:
    entra = _Entra()
    entra.down = True

    for _ in range(20):
        with pytest.raises(KeysUnavailableError):
            await entra.callers.verdict(entra.token())
    entra.seconds = LIMITS.keys_retry_s
    entra.down = False

    assert entra.fetches == 1
    assert await entra.callers.verdict(entra.token()) == "accepted"
    assert entra.fetches == 2


async def test_keys_that_could_not_be_refreshed_for_a_day_are_no_longer_trusted() -> None:
    entra = _Entra()
    await entra.callers.verdict(entra.token())
    entra.down = True
    entra.seconds = LIMITS.keys_keep_s - 1
    still_trusted = await entra.callers.verdict(entra.token())
    entra.seconds = LIMITS.keys_keep_s

    with pytest.raises(KeysUnavailableError):
        await entra.callers.verdict(entra.token())
    entra.down = False
    entra.seconds += LIMITS.keys_retry_s

    assert still_trusted == "accepted"
    assert await entra.callers.verdict(entra.token()) == "accepted"


async def test_a_token_marked_as_a_programs_is_accepted() -> None:
    entra = _Entra()

    assert await entra.callers.verdict(entra.token(idtyp="app")) == "accepted"


async def test_keys_just_fetched_are_not_fetched_again_on_request() -> None:
    entra = _Entra()
    await entra.callers.verdict(entra.token())

    assert await entra.keys.renewed() is None
    assert entra.fetches == 1


async def test_the_development_key_is_never_fetched_again() -> None:
    fixed = FixedKey(public_pem())

    assert isinstance(await fixed.current(), RSAKey)
    assert await fixed.renewed() is None


async def test_callers_are_built_from_the_settings() -> None:
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(
            lambda _request: httpx2.Response(
                200, json={"keys": [issuer_key("first").as_dict(private=False)]}
            )
        )
    ) as http:
        dev = caller_tokens(
            DevCallers(issuer=ISSUER, audience=AUDIENCE, public_key_pem=public_pem()),
            http,
            LIMITS,
        )
        entra = caller_tokens(EntraCallers(tenant_id=TENANT, audience=CLIENT_ID), http, LIMITS)
        expired_everywhere = signed(worker_claims(exp=1))

        assert await dev.verdict(expired_everywhere) == "unknown"
        assert await entra.verdict(expired_everywhere) == "unknown"

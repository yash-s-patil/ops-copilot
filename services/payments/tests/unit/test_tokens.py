"""The token the worker shows the payment service, locally and in Azure."""

import pytest
from azure.core.credentials import AccessToken
from azure.core.exceptions import ClientAuthenticationError
from azure.identity import CredentialUnavailableError
from joserfc import jwt
from joserfc.jwk import RSAKey
from pydantic import SecretStr

from opscp_payments.adapters.tokens import DevTokens, ManagedIdentityTokens
from opscp_payments.ports.payments import ProviderUnavailableError
from opscp_payments.settings import DevIssuer
from opscp_payments.sim.auth import CallerTokens, FixedKey
from opscp_payments_testing.keys import AUDIENCE, ISSUER, NOW, private_pem, public_pem

SCOPE = "api://opscp-payments/.default"


def _dev(clock: list[float]) -> DevTokens:
    issuer = DevIssuer(issuer=ISSUER, audience=AUDIENCE, private_key_pem=SecretStr(private_pem()))
    return DevTokens(issuer, now=lambda: clock[0])


async def test_the_development_token_is_one_the_simulator_accepts() -> None:
    token = await _dev([NOW]).token()
    callers = CallerTokens(
        FixedKey(public_pem()),
        issuer=ISSUER,
        audience=AUDIENCE,
        tenant_id=None,
        clock_skew_s=60,
        now=lambda: NOW,
    )

    said = jwt.decode(token, RSAKey.import_key(public_pem()), algorithms=["RS256"]).claims
    assert await callers.verdict(token) == "accepted"
    assert said == {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": NOW,
        "nbf": NOW,
        "exp": NOW + 300,
        "sub": "payments-worker",
        "oid": "payments-worker",
        "roles": ["Payments.Submit"],
    }


async def test_the_development_token_is_kept_until_a_minute_before_it_expires() -> None:
    clock = [float(NOW)]
    tokens = _dev(clock)
    first = await tokens.token()
    clock[0] = NOW + 239
    kept = await tokens.token()
    clock[0] = NOW + 240

    renewed = await tokens.token()

    assert kept == first
    assert renewed != first
    assert jwt.decode(renewed, RSAKey.import_key(public_pem())).claims["exp"] == NOW + 540


class _Credential:
    def __init__(self, failure: Exception | None = None) -> None:
        self.asked: list[tuple[str, ...]] = []
        self._failure = failure

    async def get_token(self, *scopes: str) -> AccessToken:
        self.asked.append(scopes)
        if self._failure is not None:
            raise self._failure
        return AccessToken("from-entra", NOW + 3600)


async def test_the_managed_identity_is_asked_for_exactly_the_payment_services_scope() -> None:
    credential = _Credential()

    token = await ManagedIdentityTokens(credential, SCOPE).token()

    assert (token, credential.asked) == ("from-entra", [(SCOPE,)])


@pytest.mark.parametrize(
    "failure",
    [CredentialUnavailableError("no managed identity here"), ClientAuthenticationError("denied")],
)
async def test_a_token_that_cannot_be_had_is_a_trouble_to_retry(failure: Exception) -> None:
    tokens = ManagedIdentityTokens(_Credential(failure), SCOPE)

    with pytest.raises(ProviderUnavailableError) as raised:
        await tokens.token()

    assert raised.value.trouble == "no_token"

"""Where the worker gets the token it shows the payment service."""

import time
from collections.abc import Callable
from typing import Protocol

from azure.core.credentials import AccessToken
from azure.core.exceptions import AzureError
from joserfc import jwt
from joserfc.jwk import RSAKey

from opscp_payments.ports.payments import ProviderUnavailableError
from opscp_payments.settings import SUBMIT_ROLE, WORKER_NAME, DevIssuer

ALGORITHM = "RS256"
_LIFETIME_S = 300
_RENEW_BEFORE_S = 60


class TokenCredential(Protocol):
    """The one thing asked of an Azure credential."""

    async def get_token(self, *scopes: str) -> AccessToken: ...


class ManagedIdentityTokens:
    """An app-only token for the payment service, asked for as the worker's managed identity.

    The credential keeps the token until it is close to expiry, so asking each time is cheap.
    """

    def __init__(self, credential: TokenCredential, scope: str) -> None:
        self._credential = credential
        self._scope = scope

    async def token(self) -> str:
        try:
            return (await self._credential.get_token(self._scope)).token
        except AzureError as error:
            raise ProviderUnavailableError("no_token") from error


class DevTokens:
    """A token of the same shape, signed with this machine's development key."""

    def __init__(self, issuer: DevIssuer, now: Callable[[], float] = time.time) -> None:
        self._issuer = issuer
        self._key = RSAKey.import_key(issuer.private_key_pem.get_secret_value())
        self._now = now
        self._token = ""
        self._renew_at = 0.0

    async def token(self) -> str:
        now = int(self._now())
        if now >= self._renew_at:
            claims = {
                "iss": self._issuer.issuer,
                "aud": self._issuer.audience,
                "iat": now,
                "nbf": now,
                "exp": now + _LIFETIME_S,
                "sub": WORKER_NAME,
                "oid": WORKER_NAME,
                "roles": [SUBMIT_ROLE],
            }
            self._token = jwt.encode({"alg": ALGORITHM}, claims, self._key, algorithms=[ALGORITHM])
            self._renew_at = now + _LIFETIME_S - _RENEW_BEFORE_S
        return self._token

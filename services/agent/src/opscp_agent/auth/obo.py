"""The token client: exchanges a person's token for one the MCP server accepts.

The person's token was issued for this service, and the MCP server refuses it. The
on-behalf-of exchange asks the sign-in service for a second token that names the same
person and is meant for the MCP server. Tokens are held in memory only and never stored.
"""

import asyncio
import hashlib
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import httpx2
import structlog
from azure.core.exceptions import AzureError
from azure.identity.aio import ManagedIdentityCredential
from pydantic import BaseModel, ConfigDict, Field, ValidationError

GRANT_TYPE = "urn:ietf:params:oauth:grant-type:jwt-bearer"
ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
TOKEN_USE = "on_behalf_of"  # noqa: S105 -- the name of a flow
FEDERATION_SCOPE = "api://AzureADTokenExchange/.default"
# The sign-in service's two ways of saying that the person must sign in again.
_SIGN_IN_AGAIN = frozenset({"invalid_grant", "interaction_required"})
_LOG = structlog.get_logger(__name__)


class ReauthRequiredError(Exception):
    """The person's own token is no longer accepted. Only signing in again helps."""


class TokenServiceError(Exception):
    """The sign-in service could not be reached or did not answer as it should."""


@dataclass(frozen=True, slots=True)
class AccessToken:
    value: str
    expires_at: float

    def __repr__(self) -> str:
        return f"AccessToken(expires_at={self.expires_at})"


class ClientProof(Protocol):
    async def fields(self) -> dict[str, str]:
        """The form fields that prove which service is asking. Raises TokenServiceError."""
        ...


class SecretProof:
    def __init__(self, secret: str) -> None:
        self._secret = secret

    async def fields(self) -> dict[str, str]:
        return {"client_secret": self._secret}


class FederatedProof:
    """A token for the service's managed identity, which Entra trusts in place of a secret."""

    def __init__(self, credential: ManagedIdentityCredential) -> None:
        self._credential = credential

    async def fields(self) -> dict[str, str]:
        try:
            token = await self._credential.get_token(FEDERATION_SCOPE)
        except AzureError as error:
            raise TokenServiceError("no managed identity token") from error
        return {"client_assertion_type": ASSERTION_TYPE, "client_assertion": token.token}


class TokenExchange(Protocol):
    async def exchange(self, assertion: str) -> AccessToken:
        """Raises ReauthRequiredError or TokenServiceError."""
        ...


class _Issued(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    access_token: str = Field(min_length=1)
    expires_in: int = Field(gt=0)


class _Refused(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    error: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ExchangeTarget:
    token_url: str
    client_id: str
    scope: str
    timeout_s: int


class OnBehalfOf:
    """The exchange over HTTP, as Microsoft Entra ID's token endpoint defines it."""

    def __init__(
        self,
        http: httpx2.AsyncClient,
        target: ExchangeTarget,
        proof: ClientProof,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._http = http
        self._target = target
        self._proof = proof
        self._clock = clock

    async def exchange(self, assertion: str) -> AccessToken:
        form = {
            "grant_type": GRANT_TYPE,
            "client_id": self._target.client_id,
            "assertion": assertion,
            "scope": self._target.scope,
            "requested_token_use": TOKEN_USE,
        } | await self._proof.fields()
        asked_at = self._clock()
        try:
            response = await self._http.post(
                self._target.token_url, data=form, timeout=self._target.timeout_s
            )
        except httpx2.HTTPError as error:
            raise TokenServiceError(type(error).__name__) from error
        if response.is_success:
            try:
                issued = _Issued.model_validate_json(response.content)
            except ValidationError as error:
                raise TokenServiceError("unreadable answer") from error
            return AccessToken(issued.access_token, asked_at + issued.expires_in)
        try:
            refused = _Refused.model_validate_json(response.content).error
        except ValidationError:
            refused = ""
        _LOG.warning("token_exchange_refused", status=response.status_code, error=refused[:40])
        if refused in _SIGN_IN_AGAIN:
            raise ReauthRequiredError
        raise TokenServiceError(f"status {response.status_code}")


class TokenClient:
    """Keeps each person's exchanged token until shortly before it expires.

    Tokens are kept under a hash of the token they were exchanged for, so that this object
    holds no copy of a person's own token. The least recently used are forgotten first.
    """

    def __init__(
        self,
        exchange: TokenExchange,
        *,
        refresh_before_s: int,
        kept: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._exchange = exchange
        self._refresh_before_s = refresh_before_s
        self._kept = kept
        self._clock = clock
        self._tokens: OrderedDict[str, AccessToken] = OrderedDict()
        self._asking: dict[str, asyncio.Lock] = {}

    async def token_for(self, person_token: str, *, replacing: str | None = None) -> str:
        """A token for the MCP server. `replacing` names one the server has just refused."""
        key = hashlib.sha256(person_token.encode()).hexdigest()
        lock = self._asking.setdefault(key, asyncio.Lock())
        try:
            async with lock:
                held = self._tokens.get(key)
                fresh = (
                    held is not None
                    and held.value != replacing
                    and held.expires_at - self._clock() > self._refresh_before_s
                )
                if held is None or not fresh:
                    held = await self._exchange.exchange(person_token)
                    self._tokens[key] = held
                self._tokens.move_to_end(key)
                while len(self._tokens) > self._kept:
                    self._tokens.popitem(last=False)
                return held.value
        finally:
            if not lock.locked():
                self._asking.pop(key, None)

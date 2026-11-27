"""Who may call the simulated payment service: the payments worker, and nobody else.

A token is accepted only if it is signed by the issuer this service trusts, was issued for
this service, has not expired, belongs to a program and not a person, and carries the app
role `Payments.Submit`. Any service in the tenant can obtain a token for this audience; only
the worker's identity is assigned the role.
"""

import time
from collections.abc import Callable
from typing import Literal, Protocol

import httpx2
import structlog
from joserfc import jwt
from joserfc.errors import InvalidKeyIdError, JoseError
from joserfc.jwk import KeySet, RSAKey

from opscp_common.limits import SimLimits
from opscp_payments.settings import SUBMIT_ROLE, DevCallers, EntraCallers

ALGORITHM = "RS256"
_NEVER = float("-inf")

_LOG = structlog.get_logger(__name__)

type Verdict = Literal["accepted", "unknown", "not_allowed"]
"""`unknown`: not a token this service trusts. `not_allowed`: trusted, but not the worker's."""


class KeysUnavailableError(Exception):
    """The issuer's signing keys could not be fetched and none are held."""


type Keys = RSAKey | KeySet


class SigningKeys(Protocol):
    async def current(self) -> Keys: ...

    async def renewed(self) -> Keys | None:
        """Fetched again because a token named an unknown key, or None if that is too soon."""
        ...


class FixedKey:
    def __init__(self, public_key_pem: str) -> None:
        self._key = RSAKey.import_key(public_key_pem)

    async def current(self) -> Keys:
        return self._key

    async def renewed(self) -> Keys | None:
        return None


class PublishedKeys:
    """The issuer's published key set, kept and refreshed.

    Signing keys change without notice. Microsoft's guidance is followed: refresh hourly;
    fetch again when a token names an unknown key, but no more often than every five
    minutes; keep the last good set through a failed fetch, for a day at most.
    """

    def __init__(
        self,
        http: httpx2.AsyncClient,
        url: str,
        limits: SimLimits,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http
        self._url = url
        self._limits = limits
        self._now = now
        self._keys: KeySet | None = None
        self._tried_at = _NEVER
        self._fetched_at = _NEVER

    async def current(self) -> Keys:
        if self._now() - self._fetched_at >= self._limits.keys_keep_s:
            self._keys = None
        wait = self._limits.keys_retry_s if self._keys is None else self._limits.keys_refresh_s
        if self._now() - self._tried_at >= wait:
            await self._fetch()
        if self._keys is None:
            raise KeysUnavailableError
        return self._keys

    async def renewed(self) -> Keys | None:
        if self._now() - self._tried_at < self._limits.keys_renew_min_s:
            return None
        await self._fetch()
        return self._keys

    async def _fetch(self) -> None:
        self._tried_at = self._now()
        try:
            answer = await self._http.get(self._url)
            answer.raise_for_status()
            self._keys = KeySet.import_key_set(answer.json())
            self._fetched_at = self._tried_at
        except (httpx2.HTTPError, JoseError, ValueError, TypeError) as error:
            _LOG.warning("signing_keys_not_fetched", error=type(error).__name__)


class CallerTokens:
    def __init__(
        self,
        keys: SigningKeys,
        *,
        issuer: str,
        audience: str,
        tenant_id: str | None,
        clock_skew_s: int,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._keys = keys
        self._issuer = issuer
        self._audience = audience
        self._tenant_id = tenant_id
        self._clock_skew_s = clock_skew_s
        self._now = now

    async def verdict(self, token: str) -> Verdict:
        said = await self._signed_claims(token)
        if said is None or not self._is_for_this_service(said):
            return "unknown"
        roles = said.get("roles")
        holds_role = isinstance(roles, list) and SUBMIT_ROLE in roles
        return "accepted" if _is_a_program(said) and holds_role else "not_allowed"

    async def _signed_claims(self, token: str) -> dict[str, object] | None:
        try:
            return jwt.decode(token, await self._keys.current(), algorithms=[ALGORITHM]).claims
        except InvalidKeyIdError:
            renewed = await self._keys.renewed()
        except (JoseError, ValueError):
            return None
        if renewed is None:
            return None
        try:
            return jwt.decode(token, renewed, algorithms=[ALGORITHM]).claims
        except (JoseError, ValueError):
            return None

    def _is_for_this_service(self, said: dict[str, object]) -> bool:
        now = self._now()
        audience = said.get("aud")
        expires, not_before = _seconds(said.get("exp")), _seconds(said.get("nbf", 0))
        return (
            said.get("iss") == self._issuer
            and (audience == self._audience or _names(audience, self._audience))
            and (self._tenant_id is None or said.get("tid") == self._tenant_id)
            and expires is not None
            and expires > now
            and not_before is not None
            and not_before <= now + self._clock_skew_s
        )


def _is_a_program(said: dict[str, object]) -> bool:
    """An app-only token: no delegated scope, and its subject is the program itself.

    `idtyp` is the surest sign, but Entra sends it only when the API asks for it.
    """
    subject = said.get("sub")
    return (
        "scp" not in said
        and subject is not None
        and said.get("oid") == subject
        and said.get("idtyp", "app") == "app"
    )


def _names(audience: object, wanted: str) -> bool:
    return isinstance(audience, list) and wanted in audience


def _seconds(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def caller_tokens(
    callers: EntraCallers | DevCallers, http: httpx2.AsyncClient, limits: SimLimits
) -> CallerTokens:
    """`http` fetches the issuer's keys; it is not used for the development key."""
    if isinstance(callers, DevCallers):
        return CallerTokens(
            FixedKey(callers.public_key_pem),
            issuer=callers.issuer,
            audience=callers.audience,
            tenant_id=None,
            clock_skew_s=limits.clock_skew_s,
        )
    base = f"https://{callers.authority}/{callers.tenant_id}"
    return CallerTokens(
        PublishedKeys(http, f"{base}/discovery/v2.0/keys", limits),
        issuer=f"{base}/v2.0",
        audience=callers.audience,
        tenant_id=str(callers.tenant_id),
        clock_skew_s=limits.clock_skew_s,
    )

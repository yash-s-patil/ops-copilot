"""The on-behalf-of exchange, as Microsoft Entra ID's token endpoint performs it.

A middle-tier service presents a token a person was given for it, proves which service it
is, and receives a token for the next service that names the same person. The person's
roles and region travel with them; the audience changes, so the first token is of no use
to the second service.
"""

import hmac
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import RSAKey
from joserfc.jwt import JWTClaimsRegistry

from opscp_dev_issuer.users import DEMO_USERS

ALGORITHM = "RS256"
GRANT_TYPE = "urn:ietf:params:oauth:grant-type:jwt-bearer"
# Not a grant Entra has. It stands in for a person signing in at Microsoft's own page.
DEMO_GRANT = "urn:opscp:params:oauth:grant-type:demo-user"
TOKEN_USE = "on_behalf_of"  # noqa: S105 -- the name of a flow
AGENT_SCOPE = "Agent.Access"
MCP_SCOPE = "Mcp.Access"
LIFETIME_S = 3600
_CARRIED = ("sub", "oid", "name", "roles", "region")


class RefusedError(Exception):
    """An exchange that was refused, in the words of RFC 6749 section 5.2."""

    def __init__(self, error: str, description: str, status: int = 400) -> None:
        super().__init__(description)
        self.error = error
        self.description = description
        self.status = status


@dataclass(frozen=True, slots=True)
class Client:
    """The one service allowed to exchange tokens here, and the one it may reach."""

    client_id: str
    secret: str
    audience: str
    downstream_audience: str


@dataclass(frozen=True, slots=True)
class SignInClient:
    """The web application, which signs demo users in for the agent's audience."""

    client_id: str
    secret: str
    audience: str


@dataclass(frozen=True, slots=True)
class Issued:
    access_token: str
    scope: str
    expires_in: int


class Exchange:
    def __init__(
        self,
        issuer: str,
        private_key_pem: str,
        client: Client,
        now: Callable[[], float] = time.time,
        sign_in: SignInClient | None = None,
    ) -> None:
        self._issuer = issuer
        self._key = RSAKey.import_key(private_key_pem)
        self._client = client
        self._now = now
        self._sign_in = sign_in

    def issue(self, form: Mapping[str, str]) -> Issued:
        """Answers a token request by its grant type."""
        if form.get("grant_type") == DEMO_GRANT:
            return self.demo_sign_in(form)
        return self.on_behalf_of(form)

    def demo_sign_in(self, form: Mapping[str, str]) -> Issued:
        """A token for a named demo user, as sign-in gives the web application."""
        web = self._sign_in
        if web is None:
            raise RefusedError("unsupported_grant_type", "Demo sign-in is not set up.")
        _authenticate(web.client_id, web.secret, form)
        scope = form.get("scope", "")
        if scope not in {f"{web.audience}/{AGENT_SCOPE}", f"{web.audience}/.default"}:
            raise RefusedError("invalid_scope", "This client may ask only for the agent.")
        user = DEMO_USERS.get(form.get("user", ""))
        if user is None:
            raise RefusedError("invalid_grant", "No demo user by that name.")
        now = int(self._now())
        claims: dict[str, Any] = {
            "iss": self._issuer,
            "aud": web.audience,
            "iat": now,
            "nbf": now,
            "exp": now + LIFETIME_S,
            "azp": web.client_id,
            "scp": AGENT_SCOPE,
            "sub": user.oid,
            "oid": user.oid,
            "name": user.name,
            "roles": [user.role],
        } | ({} if user.region is None else {"region": user.region})
        token = jwt.encode({"alg": ALGORITHM}, claims, self._key)
        return Issued(access_token=token, scope=scope, expires_in=LIFETIME_S)

    def on_behalf_of(self, form: Mapping[str, str]) -> Issued:
        if form.get("grant_type") != GRANT_TYPE:
            raise RefusedError("unsupported_grant_type", "Only the jwt-bearer grant is offered.")
        if form.get("requested_token_use") != TOKEN_USE:
            raise RefusedError("invalid_request", "requested_token_use must be on_behalf_of.")
        _authenticate(self._client.client_id, self._client.secret, form)
        scope = form.get("scope", "")
        allowed = {f"{self._client.downstream_audience}/{name}" for name in (".default", MCP_SCOPE)}
        if scope not in allowed:
            raise RefusedError("invalid_scope", "This client may ask only for the MCP server.")
        person = self._person(form.get("assertion", ""))
        now = int(self._now())
        claims = {
            "iss": self._issuer,
            "aud": self._client.downstream_audience,
            "iat": now,
            "nbf": now,
            "exp": now + LIFETIME_S,
            "azp": self._client.client_id,
            "scp": MCP_SCOPE,
        } | {name: person[name] for name in _CARRIED if name in person}
        token = jwt.encode({"alg": ALGORITHM}, claims, self._key)
        return Issued(access_token=token, scope=scope, expires_in=LIFETIME_S)

    def _person(self, assertion: str) -> dict[str, Any]:
        """The claims of a token this issuer signed for the calling service, still in date."""
        checks = JWTClaimsRegistry(
            now=int(self._now()),
            iss={"essential": True, "value": self._issuer},
            aud={"essential": True, "value": self._client.audience},
            exp={"essential": True},
            oid={"essential": True},
        )
        try:
            token = jwt.decode(assertion, self._key, algorithms=[ALGORITHM])
            checks.validate(token.claims)
        except (JoseError, ValueError) as error:
            raise RefusedError(
                "invalid_grant", "The assertion is not a token in date for this client."
            ) from error
        if AGENT_SCOPE not in str(token.claims.get("scp", "")).split():
            raise RefusedError("invalid_grant", "The assertion does not carry the scope.")
        return token.claims


def _authenticate(client_id: str, secret: str, form: Mapping[str, str]) -> None:
    """Refuses a caller that is not the named client. Compared in constant time."""
    known = hmac.compare_digest(form.get("client_id", "").encode(), client_id.encode())
    proven = hmac.compare_digest(form.get("client_secret", "").encode(), secret.encode())
    if not (known and proven):
        raise RefusedError("invalid_client", "The client is not known here.", status=401)

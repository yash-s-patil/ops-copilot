"""Tokens for tests, signed with a key made for the test run, and a server that trusts it."""

import base64
import time
from functools import cache
from typing import Any

from fastmcp.server.auth import RemoteAuthProvider
from joserfc import jwt
from joserfc.jwk import ECKey, OctKey, RSAKey
from pydantic import AnyHttpUrl

from opscp_mcp.authz.identity import Identity
from opscp_mcp.authz.tokens import ALGORITHM, SCOPE, sign_in_with
from opscp_mcp.settings import DevSignIn

ISSUER = "http://dev-issuer.localhost/"
AUDIENCE = "api://opscp-mcp"
PUBLIC_URL = "http://mcp.test"
KEY_ID = "test-key-1"
LIFETIME_S = 600
KEY_BITS = 2048


@cache
def signing_key() -> RSAKey:
    return RSAKey.generate_key(KEY_BITS, parameters={"kid": KEY_ID}, private=True)


@cache
def other_key() -> RSAKey:
    return RSAKey.generate_key(KEY_BITS, parameters={"kid": KEY_ID}, private=True)


def public_pem(key: RSAKey | None = None) -> str:
    return (key or signing_key()).as_pem(private=False).decode("ascii")


def public_key_setting() -> str:
    return base64.b64encode(public_pem().encode("ascii")).decode("ascii")


def claims_of(identity: Identity, **changed: Any) -> dict[str, Any]:
    """What a good token for this person holds. A change set to None removes the claim."""
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "nbf": now,
        "exp": now + LIFETIME_S,
        "sub": str(identity.oid),
        "oid": str(identity.oid),
        "name": identity.name,
        "roles": sorted(role.value for role in identity.roles),
        "scp": SCOPE,
        "region": identity.region,
    } | changed
    return {name: value for name, value in claims.items() if value is not None}


def signed(claims: dict[str, Any], key: RSAKey | OctKey | None = None, **header: str) -> str:
    key = key or signing_key()
    return jwt.encode({"alg": ALGORITHM, "kid": KEY_ID} | header, claims, key)


def token_for(identity: Identity, **changed: Any) -> str:
    return signed(claims_of(identity, **changed))


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def dev_sign_in() -> RemoteAuthProvider:
    return sign_in_with(
        DevSignIn(issuer=ISSUER, audience=AUDIENCE, public_key_pem=public_pem()),
        AnyHttpUrl(PUBLIC_URL),
    )


@cache
def approval_key_pem() -> str:
    """A P-256 key for signing approvals in tests, made once for the run."""
    return ECKey.generate_key("P-256", private=True).as_pem(private=True).decode("ascii")

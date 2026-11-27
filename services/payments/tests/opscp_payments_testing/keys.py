"""Keys and tokens for tests: a development issuer, and one the service does not trust."""

import base64
from functools import cache
from typing import Any

from joserfc import jwt
from joserfc.jwk import RSAKey

ISSUER = "http://dev-issuer.localhost/"
AUDIENCE = "api://opscp-payments"
NOW = 1_800_000_000
KEY_BITS = 2048


@cache
def issuer_key(kid: str = "dev") -> RSAKey:
    return RSAKey.generate_key(KEY_BITS, parameters={"kid": kid}, private=True)


def public_pem(kid: str = "dev") -> str:
    return issuer_key(kid).as_pem(private=False).decode("ascii")


def private_pem(kid: str = "dev") -> str:
    return issuer_key(kid).as_pem(private=True).decode("ascii")


def one_line(pem: str) -> str:
    return base64.b64encode(pem.encode("ascii")).decode("ascii")


def worker_claims(**changed: Any) -> dict[str, Any]:
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": NOW,
        "nbf": NOW,
        "exp": NOW + 300,
        "sub": "payments-worker",
        "oid": "payments-worker",
        "roles": ["Payments.Submit"],
    }
    claims.update(changed)
    return {name: value for name, value in claims.items() if value is not None}


def signed(
    claims: dict[str, Any], *, kid: str = "dev", header: dict[str, Any] | None = None
) -> str:
    head = {"alg": "RS256", "kid": kid} | (header or {})
    return jwt.encode(head, claims, issuer_key(kid), algorithms=["RS256"])

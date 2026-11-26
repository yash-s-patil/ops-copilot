"""Prints a bearer token for one of the five demo users, for local development only.

    make token WHO=priya

The token has the shape of the one Microsoft Entra ID issues for the MCP server. It is signed
with the key in .env, which only a server started with AUTH_MODE=dev accepts.
"""

import argparse
import base64
import time
from dataclasses import dataclass

from joserfc import jwt
from joserfc.jwk import RSAKey
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

LIFETIME_S = 3600
ALGORITHM = "RS256"
SCOPE = "Mcp.Access"


@dataclass(frozen=True, slots=True)
class DemoUser:
    oid: str
    name: str
    role: str
    region: str | None


DEMO_USERS = {
    "priya": DemoUser("0f1e2d3c-0001-4000-8000-000000000001", "Priya Nair", "Support.L1", "west"),
    "rohan": DemoUser("0f1e2d3c-0002-4000-8000-000000000002", "Rohan Iyer", "Support.L1", "south"),
    "arjun": DemoUser("0f1e2d3c-0003-4000-8000-000000000003", "Arjun Mehta", "Support.L2", "west"),
    "kavita": DemoUser(
        "0f1e2d3c-0004-4000-8000-000000000004", "Kavita Rao", "Support.Supervisor", "*"
    ),
    "sam": DemoUser("0f1e2d3c-0005-4000-8000-000000000005", "Sam Admin", "Platform.Admin", None),
}


def claims(user: DemoUser, *, issuer: str, audience: str, now: int) -> dict[str, object]:
    region = {} if user.region is None else {"region": user.region}
    return {
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "nbf": now,
        "exp": now + LIFETIME_S,
        "sub": user.oid,
        "oid": user.oid,
        "name": user.name,
        "roles": [user.role],
        "scp": SCOPE,
        **region,
    }


def signed(payload: dict[str, object], private_key_pem: str) -> str:
    return jwt.encode({"alg": ALGORITHM}, payload, RSAKey.import_key(private_key_pem))


class LocalIssuer(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", frozen=True, hide_input_in_errors=True)

    dev_issuer: str = Field(min_length=1)
    dev_issuer_private_key: SecretStr = Field(min_length=1)
    mcp_api_audience: str = Field(min_length=1)


def token_for(name: str) -> str:
    """A token for the named demo user, signed with the key the environment holds."""
    issuer = LocalIssuer()  # pyright: ignore[reportCallIssue] -- fields come from the environment
    private_key = base64.b64decode(issuer.dev_issuer_private_key.get_secret_value())
    payload = claims(
        DEMO_USERS[name],
        issuer=issuer.dev_issuer,
        audience=issuer.mcp_api_audience,
        now=int(time.time()),
    )
    return signed(payload, private_key.decode("ascii"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("user", choices=sorted(DEMO_USERS))
    print(token_for(parser.parse_args().user))  # noqa: T201 -- the output is the token


if __name__ == "__main__":
    main()

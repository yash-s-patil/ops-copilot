"""Prints a bearer token for one of the five demo users, for local development only.

    make token WHO=priya            a token for the agent, as sign-in gives the web application
    make token WHO=priya FOR=mcp    a token for the MCP server, to call it directly

The token has the shape of the one Microsoft Entra ID issues. It is signed with the key in
.env, which only a service started with AUTH_MODE=dev accepts. A token for one service is
refused by the other: the agent exchanges its caller's token for one meant for the MCP
server.
"""

import argparse
import base64
import time

from joserfc import jwt
from joserfc.jwk import RSAKey
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from opscp_dev_issuer.users import DEMO_USERS, DemoUser

LIFETIME_S = 3600
ALGORITHM = "RS256"
SCOPE = "Mcp.Access"
AGENT_SCOPE = "Agent.Access"
MCP, AGENT = "mcp", "agent"


def claims(
    user: DemoUser, *, issuer: str, audience: str, now: int, scope: str = SCOPE
) -> dict[str, object]:
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
        "scp": scope,
        **region,
    }


def signed(payload: dict[str, object], private_key_pem: str) -> str:
    return jwt.encode({"alg": ALGORITHM}, payload, RSAKey.import_key(private_key_pem))


class LocalIssuer(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", frozen=True, hide_input_in_errors=True)

    dev_issuer: str = Field(min_length=1)
    dev_issuer_private_key: SecretStr = Field(min_length=1)
    mcp_api_audience: str = Field(min_length=1)
    agent_api_audience: str = Field(min_length=1)


def token_for(name: str, service: str = MCP) -> str:
    """A token for the named demo user and service, signed with the key the environment holds."""
    issuer = LocalIssuer()  # pyright: ignore[reportCallIssue] -- fields come from the environment
    private_key = base64.b64decode(issuer.dev_issuer_private_key.get_secret_value())
    for_agent = service == AGENT
    payload = claims(
        DEMO_USERS[name],
        issuer=issuer.dev_issuer,
        audience=issuer.agent_api_audience if for_agent else issuer.mcp_api_audience,
        now=int(time.time()),
        scope=AGENT_SCOPE if for_agent else SCOPE,
    )
    return signed(payload, private_key.decode("ascii"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("user", choices=sorted(DEMO_USERS))
    parser.add_argument("--for", dest="service", choices=(AGENT, MCP), default=AGENT)
    asked = parser.parse_args()
    print(token_for(asked.user, asked.service))  # noqa: T201 -- the output is the token


if __name__ == "__main__":
    main()

"""Sign-in: which bearer tokens this service accepts, and who each one says is calling.

The service accepts only tokens issued for itself. It reads who the caller is and what
role they hold; what that role may do with business data is decided by the MCP server.
"""

from typing import Annotated
from uuid import UUID

from fastmcp.server.auth import TokenVerifier
from fastmcp.server.auth.providers.azure import AzureJWTVerifier
from fastmcp.server.auth.providers.jwt import JWTVerifier
from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

from opscp_agent.settings import DevSignIn, EntraSignIn, SignIn

SCOPE = "Agent.Access"
ALGORITHM = "RS256"
ADMIN_ROLE = "Platform.Admin"


class Person(BaseModel):
    """The signed-in person as their token names them."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    oid: UUID
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    roles: tuple[str, ...] = ()
    region: str | None = None

    @property
    def is_admin(self) -> bool:
        return ADMIN_ROLE in self.roles


class Caller(BaseModel):
    """A person and the token they came with, which is exchanged and never passed on."""

    model_config = ConfigDict(frozen=True)

    person: Person
    token: Annotated[str, StringConstraints(min_length=1)]

    def __repr__(self) -> str:
        return f"Caller(oid={self.person.oid})"

    def __str__(self) -> str:
        return repr(self)


def token_verifier(sign_in: SignIn) -> JWTVerifier:
    """The check of signature, issuer, audience, expiry and scope."""
    match sign_in:
        case EntraSignIn():
            return AzureJWTVerifier(
                client_id=str(sign_in.client_id),
                tenant_id=str(sign_in.tenant_id),
                required_scopes=[SCOPE],
                identifier_uri=sign_in.audience,
                base_authority=sign_in.authority,
            )
        case DevSignIn():
            return JWTVerifier(
                public_key=sign_in.public_key_pem,
                issuer=sign_in.issuer,
                audience=sign_in.audience,
                algorithm=ALGORITHM,
                required_scopes=[SCOPE],
            )


class Callers:
    def __init__(self, verifier: TokenVerifier) -> None:
        self._verifier = verifier

    async def caller_of(self, token: str) -> Caller | None:
        """None unless the token is one this service accepts, expires, and names a person."""
        verified = await self._verifier.verify_token(token) if token else None
        if verified is None or verified.expires_at is None:
            return None
        try:
            return Caller(person=Person.model_validate(verified.claims), token=token)
        except ValidationError:
            return None

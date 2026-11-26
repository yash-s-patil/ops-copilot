"""Sign-in: which bearer tokens this server accepts, and who each one says is calling.

The server accepts only tokens issued for itself: the signature, issuer, audience, expiry
and scope are checked on every request. A caller's token is read here and goes nowhere
else; backends are reached with the server's own identity.
"""

import structlog
from fastmcp.server.auth import AccessToken, RemoteAuthProvider, TokenVerifier
from fastmcp.server.auth.providers.azure import AzureJWTVerifier
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.dependencies import get_access_token
from pydantic import AnyHttpUrl, ValidationError

from opscp_common.errors import PermissionDeniedError
from opscp_mcp.authz.identity import Identity, identity_from_claims
from opscp_mcp.settings import DevSignIn, EntraSignIn, SignIn

SCOPE = "Mcp.Access"
ALGORITHM = "RS256"
RESOURCE_NAME = "Meridian Ops Copilot MCP server"

_LOG = structlog.get_logger(__name__)


class CallerTokens(TokenVerifier):
    """Accepts what `signed` accepts, provided the token expires and names a caller."""

    def __init__(self, signed: JWTVerifier) -> None:
        super().__init__(required_scopes=signed.required_scopes)
        self._signed = signed

    @property
    def scopes_supported(self) -> list[str]:
        return self._signed.scopes_supported

    def get_challenge_scopes(self, required_scopes: list[str] | None = None) -> list[str]:
        return self._signed.get_challenge_scopes(required_scopes)

    async def verify_token(self, token: str) -> AccessToken | None:
        verified = await self._signed.verify_token(token)
        if verified is None:
            return None
        if verified.expires_at is None:
            _LOG.warning("token_refused", reason="it never expires")
            return None
        try:
            identity_from_claims(verified.claims)
        except ValidationError as error:
            claims = sorted({str(issue["loc"][0]) for issue in error.errors() if issue["loc"]})
            _LOG.warning("token_refused", reason="it does not name a caller", claims=claims)
            return None
        return verified


class TokenIdentities:
    """The caller of the current request, read from the token that was verified for it."""

    def current(self) -> Identity:
        token = get_access_token()
        if token is None:
            raise PermissionDeniedError("Sign in to use this server.")
        return identity_from_claims(token.claims)


def _signed_tokens(sign_in: SignIn) -> tuple[JWTVerifier, str]:
    """The check of signature, issuer, audience, expiry and scope, and the issuer's address."""
    match sign_in:
        case EntraSignIn():
            issuer = f"https://{sign_in.authority}/{sign_in.tenant_id}/v2.0"
            entra = AzureJWTVerifier(
                client_id=str(sign_in.client_id),
                tenant_id=str(sign_in.tenant_id),
                required_scopes=[SCOPE],
                identifier_uri=sign_in.audience,
                base_authority=sign_in.authority,
            )
            return entra, issuer
        case DevSignIn():
            local = JWTVerifier(
                public_key=sign_in.public_key_pem,
                issuer=sign_in.issuer,
                audience=sign_in.audience,
                algorithm=ALGORITHM,
                required_scopes=[SCOPE],
            )
            return local, sign_in.issuer


def sign_in_with(sign_in: SignIn, public_url: AnyHttpUrl) -> RemoteAuthProvider:
    """What the MCP endpoint is protected with: token checks, and where to get a token."""
    signed, issuer = _signed_tokens(sign_in)
    return RemoteAuthProvider(
        token_verifier=CallerTokens(signed),
        authorization_servers=[AnyHttpUrl(issuer)],
        base_url=public_url,
        resource_name=RESOURCE_NAME,
    )

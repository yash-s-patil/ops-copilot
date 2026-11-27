"""Approval tokens signed with a key held in this process, for local development.

In Azure the key stays inside Key Vault and the server asks it to sign. Locally there is no
Key Vault, so `make env` generates a key and the server holds it.
"""

from datetime import UTC, datetime

from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import ECKey
from pydantic import ValidationError

from opscp_common.errors import BackendUnavailableError
from opscp_mcp.ports.signing import ApprovalClaims

ALGORITHM = "ES256"
ISSUER = "opscp-mcp"
AUDIENCE = "opscp-mcp/approval"
_CURVE = "P-256"
_WRONG_KEY = "The approval signing key must be a P-256 private key"


class LocalApprovalSigner:
    def __init__(self, private_key_pem: str) -> None:
        try:
            self._key = ECKey.import_key(private_key_pem)
        except (JoseError, ValueError) as error:
            raise ValueError(_WRONG_KEY) from error
        if self._key.curve_name != _CURVE or not self._key.is_private:
            raise ValueError(_WRONG_KEY)

    async def sign(self, claims: ApprovalClaims) -> str:
        payload = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "jti": str(claims.token_id),
            "sub": str(claims.requester_oid),
            "act": str(claims.approver_oid),
            "action_id": str(claims.action_id),
            "tool": claims.tool,
            "args_hash": claims.args_hash,
            "iat": int(claims.issued_at.timestamp()),
            "exp": int(claims.expires_at.timestamp()),
        }
        return jwt.encode({"alg": ALGORITHM}, payload, self._key, algorithms=[ALGORITHM])

    async def verify(self, token: str) -> ApprovalClaims | None:
        try:
            said = jwt.decode(token, self._key, algorithms=[ALGORITHM]).claims
            if (said.get("iss"), said.get("aud")) != (ISSUER, AUDIENCE):
                return None
            return ApprovalClaims(
                token_id=said["jti"],
                requester_oid=said["sub"],
                approver_oid=said["act"],
                action_id=said["action_id"],
                tool=said["tool"],
                args_hash=said["args_hash"],
                issued_at=datetime.fromtimestamp(said["iat"], UTC),
                expires_at=datetime.fromtimestamp(said["exp"], UTC),
            )
        except (JoseError, KeyError, TypeError, ValueError, OverflowError, ValidationError):
            return None


class NoApprovalSigner:
    """Stands where the Key Vault signer will: approvals are refused, nothing else is."""

    async def sign(self, claims: ApprovalClaims) -> str:
        raise BackendUnavailableError("Approvals cannot be signed right now. Try again later.")

    async def verify(self, token: str) -> ApprovalClaims | None:
        return None

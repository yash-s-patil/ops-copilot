"""Signing and checking approval tokens, wherever the key is kept."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from opscp_mcp.ports.actions import WriteTool
from opscp_mcp.ports.base import Record


class ApprovalClaims(Record):
    """What an approval token says: who asked, who approved, and exactly what for."""

    token_id: UUID
    requester_oid: UUID
    approver_oid: UUID
    action_id: UUID
    tool: WriteTool
    args_hash: str
    issued_at: datetime
    expires_at: datetime


class ApprovalSigner(Protocol):
    async def sign(self, claims: ApprovalClaims) -> str:
        """A compact signed token carrying the claims. The private key is never handed out."""
        ...

    async def verify(self, token: str) -> ApprovalClaims | None:
        """The claims of a token this signer made, or None for anything else.

        Expiry is not judged here: the caller compares `expires_at` with its own clock.
        """
        ...

"""Who the caller is and what their role allows, as this server decides it.

The agent service shows a person their own permissions. It asks here, so that the mapping
from role to permission lives in one place.
"""

from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from opscp_mcp.rest.approvals import Answered, SignedIn

IDENTITY_PATH = "/identity"

router = APIRouter(route_class=Answered)


class IdentityView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    oid: UUID
    name: str
    roles: tuple[str, ...]
    region: str | None
    permissions: tuple[str, ...]


@router.get(IDENTITY_PATH)
async def identity(caller: SignedIn) -> IdentityView:
    return IdentityView(
        oid=caller.oid,
        name=caller.name,
        roles=tuple(sorted(role.value for role in caller.roles)),
        region=caller.region,
        permissions=tuple(sorted(permission.value for permission in caller.permissions)),
    )

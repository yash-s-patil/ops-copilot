"""Who is calling, as established from their token."""

from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from opscp_common.errors import PermissionDeniedError
from opscp_common.vocabulary import CallerRegion
from opscp_mcp.authz.policy import Permission, Role, permissions_for


class Identity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    oid: UUID
    name: str = Field(min_length=1)
    roles: frozenset[Role]
    region: CallerRegion | None

    @property
    def permissions(self) -> frozenset[Permission]:
        return permissions_for(self.roles)

    def can(self, permission: Permission) -> bool:
        return permission in self.permissions

    def require(self, permission: Permission) -> None:
        if not self.can(permission):
            raise PermissionDeniedError(
                "Your role does not allow this.", permission=permission.value
            )


class IdentityResolver(Protocol):
    def current(self) -> Identity:
        """The verified caller of the request being handled.

        Raises PermissionDeniedError when the request carries no verified identity.
        """
        ...


class NoIdentities:
    """Used until sign-in is configured: nobody is recognised, so every call is refused."""

    def current(self) -> Identity:
        raise PermissionDeniedError("This server has no sign-in configured, so it serves nobody.")

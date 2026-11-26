"""Who is calling, as established from their token."""

from collections.abc import Mapping
from typing import Annotated, Protocol, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from opscp_common.errors import PermissionDeniedError
from opscp_common.vocabulary import ALL_REGIONS, CallerRegion
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


class _Claims(BaseModel):
    """The claims of a verified token that say who is calling. Other claims are not read."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    oid: UUID
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    roles: tuple[str, ...] = ()
    region: CallerRegion | None = None

    @model_validator(mode="after")
    def _only_a_supervisor_spans_regions(self) -> Self:
        if self.region == ALL_REGIONS and Role.SUPERVISOR.value not in self.roles:
            raise ValueError("only a supervisor's region may be every region")
        return self


def identity_from_claims(claims: Mapping[str, object]) -> Identity:
    """Raises pydantic.ValidationError when the claims do not describe a caller.

    A role this server does not define grants nothing, so it is left out.
    """
    read = _Claims.model_validate(claims)
    known = {role.value for role in Role}
    return Identity(
        oid=read.oid,
        name=read.name,
        roles=frozenset(Role(role) for role in read.roles if role in known),
        region=read.region,
    )


class IdentityResolver(Protocol):
    def current(self) -> Identity:
        """The verified caller of the request being handled.

        Raises PermissionDeniedError when the request carries no verified identity.
        """
        ...

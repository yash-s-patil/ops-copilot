from uuid import UUID

import pytest
from pydantic import ValidationError

from opscp_common.errors import PermissionDeniedError
from opscp_mcp.authz.identity import Identity
from opscp_mcp.authz.policy import Permission, Role, permissions_for

L1, L2, SUPERVISOR, ADMIN = Role.L1, Role.L2, Role.SUPERVISOR, Role.ADMIN
MATRIX: dict[Permission, set[Role]] = {
    Permission.CUSTOMERS_READ: {L1, L2, SUPERVISOR},
    Permission.ORDERS_READ: {L1, L2, SUPERVISOR},
    Permission.TICKETS_READ: {L1, L2, SUPERVISOR},
    Permission.RUNBOOKS_READ: {L1, L2, SUPERVISOR},
    Permission.POLICIES_READ: {L1, L2, SUPERVISOR},
    Permission.TICKETS_WRITE: {L1, L2, SUPERVISOR},
    Permission.PII_READ: {L2, SUPERVISOR},
    Permission.REFUNDS_REQUEST: {L2, SUPERVISOR},
    Permission.REFUNDS_APPROVE: {SUPERVISOR},
    Permission.ADMIN_READ: {ADMIN},
    Permission.CHAOS_WRITE: {ADMIN},
}
CELLS = [(permission, role) for permission in Permission for role in Role]


def _identity(*roles: Role) -> Identity:
    return Identity(
        oid=UUID("0f1e2d3c-0001-4000-8000-000000000001"),
        name="Priya Nair",
        roles=frozenset(roles),
        region="west",
    )


def test_the_matrix_covers_every_permission() -> None:
    assert set(MATRIX) == set(Permission)


@pytest.mark.parametrize(
    ("permission", "role"),
    CELLS,
    ids=[f"{role.value}:{permission.value}" for permission, role in CELLS],
)
def test_each_role_holds_exactly_the_permissions_in_the_matrix(
    permission: Permission, role: Role
) -> None:
    assert (permission in permissions_for([role])) is (role in MATRIX[permission])


def test_several_roles_combine_their_permissions() -> None:
    combined = permissions_for([L1, ADMIN])

    assert Permission.TICKETS_WRITE in combined
    assert Permission.CHAOS_WRITE in combined
    assert Permission.REFUNDS_REQUEST not in combined


def test_an_admin_alone_holds_no_permission_over_customer_data() -> None:
    assert permissions_for([ADMIN]) == {Permission.ADMIN_READ, Permission.CHAOS_WRITE}


def test_an_unknown_role_is_refused_not_ignored() -> None:
    with pytest.raises(ValidationError):
        Identity.model_validate(
            {
                "oid": "0f1e2d3c-0001-4000-8000-000000000001",
                "name": "x",
                "roles": ["Support.L3"],
                "region": "west",
            }
        )


def test_no_role_means_no_permission() -> None:
    assert permissions_for([]) == frozenset()


def test_an_identity_can_do_what_its_roles_allow() -> None:
    priya = _identity(L1)

    assert priya.can(Permission.TICKETS_WRITE)
    assert not priya.can(Permission.REFUNDS_REQUEST)
    priya.require(Permission.ORDERS_READ)


def test_a_missing_permission_is_refused_and_named() -> None:
    with pytest.raises(PermissionDeniedError) as refusal:
        _identity(L1).require(Permission.PII_READ)

    assert refusal.value.body().details == {"permission": "pii.read"}


def test_an_identity_cannot_be_given_another_role_afterwards() -> None:
    priya = _identity(L1)

    with pytest.raises(ValueError, match="frozen"):
        priya.roles = frozenset({SUPERVISOR})


def test_permissions_cannot_be_assigned_directly() -> None:
    priya = _identity(L1)

    with pytest.raises((AttributeError, ValueError)):
        priya.permissions = frozenset(Permission)  # pyright: ignore[reportAttributeAccessIssue] -- the assignment is the point


def test_a_copy_with_fewer_roles_loses_the_permissions() -> None:
    kavita = _identity(SUPERVISOR)
    assert kavita.can(Permission.REFUNDS_APPROVE)

    demoted = kavita.model_copy(update={"roles": frozenset({L1})})

    assert not demoted.can(Permission.REFUNDS_APPROVE)
    assert not demoted.can(Permission.PII_READ)


def test_an_identity_may_have_no_region() -> None:
    sam = Identity(
        oid=UUID("0f1e2d3c-0005-4000-8000-000000000005"),
        name="Sam Admin",
        roles=frozenset({ADMIN}),
        region=None,
    )

    assert sam.region is None

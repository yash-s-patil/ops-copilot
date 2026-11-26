from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from opscp_mcp.authz.identity import identity_from_claims
from opscp_mcp.authz.policy import Permission, Role
from opscp_mcp_testing.identities import ARJUN, KAVITA, PRIYA, SAM
from opscp_mcp_testing.signin import claims_of

OID = "0f1e2d3c-0001-4000-8000-000000000001"


@pytest.mark.parametrize("identity", [PRIYA, ARJUN, KAVITA, SAM])
def test_a_person_is_read_back_from_their_own_token(identity: Any) -> None:
    assert identity_from_claims(claims_of(identity)) == identity


def test_only_the_claims_that_name_the_caller_are_read() -> None:
    identity = identity_from_claims(
        {"oid": OID, "name": "Priya Nair", "permissions": ["refunds.approve"], "groups": ["admins"]}
    )

    assert (identity.oid, identity.roles, identity.region) == (UUID(OID), frozenset(), None)
    assert identity.permissions == frozenset()


def test_roles_grant_what_the_policy_says_and_nothing_a_token_adds() -> None:
    identity = identity_from_claims(
        {"oid": OID, "name": "A", "roles": ["Support.L1", "Support.L1", "refunds.approve", ""]}
    )

    assert identity.roles == {Role.L1}
    assert not identity.can(Permission.REFUNDS_APPROVE)


@pytest.mark.parametrize(
    "changed",
    [
        {"oid": None},
        {"oid": "not-an-id"},
        {"oid": 7},
        {"name": None},
        {"name": ""},
        {"name": ["Priya"]},
        {"roles": "Support.L1"},
        {"roles": {"Support.L1": True}},
        {"roles": [1]},
        {"region": "north"},
        {"region": ""},
        {"region": ["west"]},
        {"region": "WEST"},
        {"region": "*"},
        {"region": "*", "roles": ["Support.L2", "Platform.Admin"]},
        {"name": "   "},
    ],
    ids=str,
)
def test_claims_that_do_not_describe_a_caller_are_refused(changed: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        identity_from_claims(claims_of(PRIYA) | changed)


def test_a_name_is_read_without_the_space_around_it() -> None:
    assert identity_from_claims(claims_of(PRIYA) | {"name": " Priya Nair "}).name == "Priya Nair"

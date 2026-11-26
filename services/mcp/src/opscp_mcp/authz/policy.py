"""The only place that decides what a role may do."""

from collections.abc import Iterable, Mapping
from enum import StrEnum
from types import MappingProxyType


class Role(StrEnum):
    L1 = "Support.L1"
    L2 = "Support.L2"
    SUPERVISOR = "Support.Supervisor"
    ADMIN = "Platform.Admin"


class Permission(StrEnum):
    CUSTOMERS_READ = "customers.read"
    ORDERS_READ = "orders.read"
    TICKETS_READ = "tickets.read"
    RUNBOOKS_READ = "runbooks.read"
    POLICIES_READ = "policies.read"
    TICKETS_WRITE = "tickets.write"
    PII_READ = "pii.read"
    REFUNDS_REQUEST = "refunds.request"
    REFUNDS_APPROVE = "refunds.approve"
    ADMIN_READ = "admin.read"
    CHAOS_WRITE = "chaos.write"


_L1 = frozenset(
    {
        Permission.CUSTOMERS_READ,
        Permission.ORDERS_READ,
        Permission.TICKETS_READ,
        Permission.RUNBOOKS_READ,
        Permission.POLICIES_READ,
        Permission.TICKETS_WRITE,
    }
)
_L2 = _L1 | {Permission.PII_READ, Permission.REFUNDS_REQUEST}

_GRANTS: Mapping[Role, frozenset[Permission]] = MappingProxyType(
    {
        Role.L1: _L1,
        Role.L2: _L2,
        Role.SUPERVISOR: _L2 | {Permission.REFUNDS_APPROVE},
        Role.ADMIN: frozenset({Permission.ADMIN_READ, Permission.CHAOS_WRITE}),
    }
)


def permissions_for(roles: Iterable[Role]) -> frozenset[Permission]:
    return frozenset[Permission]().union(*(_GRANTS[role] for role in roles))

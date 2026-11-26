from uuid import UUID

from opscp_mcp.authz.identity import Identity
from opscp_mcp.authz.policy import Role

PRIYA = Identity(
    oid=UUID("0f1e2d3c-0001-4000-8000-000000000001"),
    name="Priya Nair",
    roles=frozenset({Role.L1}),
    region="west",
)
ROHAN = Identity(
    oid=UUID("0f1e2d3c-0002-4000-8000-000000000002"),
    name="Rohan Iyer",
    roles=frozenset({Role.L1}),
    region="south",
)
ARJUN = Identity(
    oid=UUID("0f1e2d3c-0003-4000-8000-000000000003"),
    name="Arjun Mehta",
    roles=frozenset({Role.L2}),
    region="west",
)
KAVITA = Identity(
    oid=UUID("0f1e2d3c-0004-4000-8000-000000000004"),
    name="Kavita Rao",
    roles=frozenset({Role.SUPERVISOR}),
    region="*",
)
SAM = Identity(
    oid=UUID("0f1e2d3c-0005-4000-8000-000000000005"),
    name="Sam Admin",
    roles=frozenset({Role.ADMIN}),
    region=None,
)

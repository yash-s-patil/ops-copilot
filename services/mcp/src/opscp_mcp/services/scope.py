from opscp_common.errors import PermissionDeniedError
from opscp_mcp.authz.identity import Identity
from opscp_mcp.ports.meridian import Scope


def scope_of(identity: Identity) -> Scope:
    if identity.region is None:
        raise PermissionDeniedError("Your account has no region, so it cannot read records.")
    return Scope(user_oid=identity.oid, region=identity.region)

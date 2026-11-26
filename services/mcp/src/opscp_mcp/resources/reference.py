"""The reference resources. Each read reports the version of the text it returns."""

from fastmcp import Context, FastMCP
from fastmcp.resources import ResourceContent, ResourceResult

from opscp_mcp.authz.policy import Permission
from opscp_mcp.services.health import HEALTH_URI
from opscp_mcp.services.resources import (
    ORDER_LIFECYCLE_URI,
    REFUND_SUMMARY_URI,
    RUNBOOK_INDEX_URI,
    Reference,
)
from opscp_mcp.tools.runner import services_of

VERSION_KEY = "opscp/version"
_PLAIN_TEXT = "text/plain"
_JSON = "application/json"
REQUIRES = {
    ORDER_LIFECYCLE_URI: Permission.ORDERS_READ,
    REFUND_SUMMARY_URI: Permission.POLICIES_READ,
    RUNBOOK_INDEX_URI: Permission.RUNBOOKS_READ,
}


def _result(reference: Reference) -> ResourceResult:
    meta = {VERSION_KEY: reference.version}
    return ResourceResult(
        contents=[ResourceContent(content=reference.text, mime_type=_PLAIN_TEXT, meta=meta)],
        meta=meta,
    )


OPEN_TO_ANY_ROLE = frozenset({HEALTH_URI})


def register(server: FastMCP) -> None:
    @server.resource(
        ORDER_LIFECYCLE_URI,
        name="order-lifecycle",
        title="Order lifecycle",
        description="Order statuses, the moves allowed between them, and when an order is stuck.",
        mime_type=_PLAIN_TEXT,
    )
    async def order_lifecycle(ctx: Context) -> ResourceResult:
        services = services_of(ctx)
        return _result(services.references.order_lifecycle(services.identities.current()))

    @server.resource(
        REFUND_SUMMARY_URI,
        name="refund-summary",
        title="Refund rules in brief",
        description="The refund rules in brief, with the policy section that holds each in full.",
        mime_type=_PLAIN_TEXT,
    )
    async def refund_summary(ctx: Context) -> ResourceResult:
        services = services_of(ctx)
        return _result(services.references.refund_summary(services.identities.current()))

    @server.resource(
        RUNBOOK_INDEX_URI,
        name="runbook-index",
        title="Runbook index",
        description="The id, title and one-line summary of every runbook.",
        mime_type=_PLAIN_TEXT,
    )
    async def runbook_index(ctx: Context) -> ResourceResult:
        services = services_of(ctx)
        return _result(await services.references.runbook_index(services.identities.current()))

    @server.resource(
        HEALTH_URI,
        name="server-health",
        title="Server health",
        description=(
            "The state of each system this server relies on (ok, recovering or unavailable) "
            "and the tools that cannot be relied on right now. Read it again rather than "
            "remembering it."
        ),
        mime_type=_JSON,
    )
    async def server_health(ctx: Context) -> ResourceResult:
        services = services_of(ctx)
        health = services.health.report(services.identities.current())
        return ResourceResult(
            contents=[ResourceContent(content=health.model_dump_json(), mime_type=_JSON)]
        )

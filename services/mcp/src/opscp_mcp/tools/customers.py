from typing import Annotated

from fastmcp import Context, FastMCP
from pydantic import Field

from opscp_common.limits import Limits
from opscp_mcp.authz.policy import Permission
from opscp_mcp.services.customers import CustomerView
from opscp_mcp.tools.models import ToolOutput
from opscp_mcp.tools.runner import answer
from opscp_mcp.tools.shared import READ_ONLY

LOOKUP_CUSTOMER = "lookup_customer"
REQUIRES = {LOOKUP_CUSTOMER: Permission.CUSTOMERS_READ}


def register(server: FastMCP, limits: Limits) -> None:
    @server.tool(
        name=LOOKUP_CUSTOMER,
        title="Look up a customer",
        annotations=READ_ONLY,
        description=(
            "Find one customer by id or by email address. Give exactly one of the two. "
            "Returns the name, tier, region, contact details and the number of open tickets. "
            "Contact details are masked unless the caller may see them. "
            "A customer outside the caller's region is reported as not found."
        ),
    )
    async def lookup_customer(
        ctx: Context,
        customer_id: Annotated[int | None, Field(description="The customer's number.")] = None,
        email: Annotated[str | None, Field(description="The customer's email address.")] = None,
    ) -> ToolOutput[CustomerView]:
        return await answer(
            ctx,
            LOOKUP_CUSTOMER,
            limits.tool_timeout.lookup_customer_s,
            lambda services, identity: services.customers.lookup(
                identity, customer_id=customer_id, email=email
            ),
        )

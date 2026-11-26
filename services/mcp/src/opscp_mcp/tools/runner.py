"""What every tool does around its one service call."""

import asyncio
from collections.abc import Awaitable, Callable

from fastmcp import Context

from opscp_common.errors import BackendTimeoutError, InternalError
from opscp_mcp.authz.identity import Identity
from opscp_mcp.container import Services
from opscp_mcp.services.caps import Result
from opscp_mcp.tools.models import Provenance, ToolOutput

SERVICES_KEY = "services"


def services_of(ctx: Context) -> Services:
    services = ctx.lifespan_context.get(SERVICES_KEY)
    if not isinstance(services, Services):
        raise InternalError
    return services


async def answer[T: Result](
    ctx: Context,
    tool: str,
    timeout_s: int,
    call: Callable[[Services, Identity], Awaitable[T]],
) -> ToolOutput[T]:
    """Identify the caller, make the call within the tool's time limit, and stamp the result."""
    services = services_of(ctx)
    identity = services.identities.current()
    try:
        async with asyncio.timeout(timeout_s):
            data = await call(services, identity)
    except TimeoutError as error:
        raise BackendTimeoutError("The request took too long. Try again.") from error
    return ToolOutput(
        data=data,
        provenance=Provenance(
            id=f"{tool}-{services.new_result_id()}", tool=tool, retrieved_at=services.clock()
        ),
    )

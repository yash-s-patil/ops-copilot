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


TOO_SLOW = "The request took too long. Try again."


async def within[T](timeout_s: int, work: Awaitable[T]) -> T:
    """The work's result, or TIMEOUT for the caller when it takes longer than allowed."""
    try:
        async with asyncio.timeout(timeout_s):
            return await work
    except TimeoutError as error:
        raise BackendTimeoutError(TOO_SLOW) from error


def services_in(request: Context | None) -> Services:
    """For middleware, which is handed a context that the framework types as optional."""
    if request is None:
        raise InternalError
    return services_of(request)


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
    data = await within(timeout_s, call(services, identity))
    return ToolOutput(
        data=data,
        provenance=Provenance(
            id=f"{tool}-{services.new_result_id()}", tool=tool, retrieved_at=services.clock()
        ),
    )

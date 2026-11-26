"""Everything a request handler needs, built once at start-up and passed in, never global."""

from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncEngine

from opscp_common.blob import policies_container
from opscp_common.limits import Limits
from opscp_common.settings import StorageSettings
from opscp_mcp.adapters.blob import BlobPolicies
from opscp_mcp.adapters.memory_runbooks import InMemoryRunbooks
from opscp_mcp.adapters.postgres.customers import PostgresCustomers
from opscp_mcp.adapters.postgres.orders import PostgresOrders
from opscp_mcp.adapters.postgres.tickets import PostgresTickets
from opscp_mcp.authz.identity import IdentityResolver
from opscp_mcp.authz.tokens import TokenIdentities
from opscp_mcp.services.customers import CustomerService
from opscp_mcp.services.orders import Clock, OrderService
from opscp_mcp.services.policies import PolicyService
from opscp_mcp.services.resources import ReferenceService
from opscp_mcp.services.runbooks import RunbookService
from opscp_mcp.services.tickets import TicketService
from opscp_mcp.settings import McpSettings

_ID_HEX_CHARS = 12

type OpenServices = Callable[[], AbstractAsyncContextManager[Services]]


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_result_id() -> str:
    return uuid4().hex[:_ID_HEX_CHARS]


@dataclass(frozen=True, slots=True, kw_only=True)
class Services:
    identities: IdentityResolver
    customers: CustomerService
    orders: OrderService
    tickets: TicketService
    runbooks: RunbookService
    policies: PolicyService
    references: ReferenceService
    clock: Clock = utc_now
    new_result_id: Callable[[], str] = new_result_id


def production_services(
    settings: McpSettings, storage: StorageSettings, limits: Limits, engine: AsyncEngine
) -> OpenServices:
    @asynccontextmanager
    async def open_services() -> AsyncGenerator[Services]:
        runbooks = InMemoryRunbooks.from_directory(settings.runbooks_dir)
        async with policies_container(storage) as container:
            policies = BlobPolicies(
                container,
                timeout_s=limits.timeout.blob_s,
                max_bytes=limits.policy.document_bytes,
            )
            yield Services(
                identities=TokenIdentities(),
                customers=CustomerService(PostgresCustomers(engine)),
                orders=OrderService(PostgresOrders(engine), limits, utc_now),
                tickets=TicketService(PostgresTickets(engine), limits, utc_now),
                runbooks=RunbookService(runbooks, limits),
                policies=PolicyService(policies, limits),
                references=ReferenceService(runbooks, limits),
            )

    return open_services

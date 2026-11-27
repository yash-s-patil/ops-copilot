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
from opscp_mcp.adapters.blob import classify as classify_blob
from opscp_mcp.adapters.local_signer import LocalApprovalSigner, NoApprovalSigner
from opscp_mcp.adapters.memory_runbooks import InMemoryRunbooks
from opscp_mcp.adapters.postgres.actions import PostgresPendingActions, PostgresRefundCases
from opscp_mcp.adapters.postgres.audit import PostgresAuditTrail
from opscp_mcp.adapters.postgres.customers import PostgresCustomers
from opscp_mcp.adapters.postgres.execution import PostgresExecutions
from opscp_mcp.adapters.postgres.orders import PostgresOrders
from opscp_mcp.adapters.postgres.rate_limits import PostgresRateLimiter
from opscp_mcp.adapters.postgres.refunds import PostgresRefundRetries
from opscp_mcp.adapters.postgres.session import Database
from opscp_mcp.adapters.postgres.session import classify as classify_postgres
from opscp_mcp.adapters.postgres.tickets import PostgresTickets
from opscp_mcp.authz.identity import IdentityResolver
from opscp_mcp.authz.tokens import TokenIdentities
from opscp_mcp.ports.controls import AuditTrail, RateLimiter
from opscp_mcp.reliability.breaker import CircuitBreaker
from opscp_mcp.reliability.guard import Classify, Guard, GuardLimits
from opscp_mcp.services.approvals import ApprovalService
from opscp_mcp.services.customers import CustomerService
from opscp_mcp.services.execution import ExecutionService
from opscp_mcp.services.health import HealthService
from opscp_mcp.services.orders import Clock, OrderService
from opscp_mcp.services.policies import PolicyService
from opscp_mcp.services.proposals import ProposalService
from opscp_mcp.services.rate import OneAtATime
from opscp_mcp.services.refunds import RefundService
from opscp_mcp.services.resources import ReferenceService
from opscp_mcp.services.runbooks import RunbookService
from opscp_mcp.services.tickets import TicketService
from opscp_mcp.settings import McpSettings
from opscp_mcp.tools.names import (
    GET_ORDER,
    GET_POLICY_DOCUMENT,
    GET_TICKET,
    ISSUE_REFUND,
    LIST_TICKETS,
    LOOKUP_CUSTOMER,
    SEARCH_RUNBOOKS,
    UPDATE_TICKET,
)

_ID_HEX_CHARS = 12

type OpenServices = Callable[[], AbstractAsyncContextManager[Services]]


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_result_id() -> str:
    return uuid4().hex[:_ID_HEX_CHARS]


@dataclass(frozen=True, slots=True, kw_only=True)
class Services:
    identities: IdentityResolver
    rates: RateLimiter
    audit: AuditTrail
    customers: CustomerService
    orders: OrderService
    tickets: TicketService
    runbooks: RunbookService
    policies: PolicyService
    references: ReferenceService
    proposals: ProposalService
    approvals: ApprovalService
    refunds: RefundService
    health: HealthService
    clock: Clock = utc_now
    new_result_id: Callable[[], str] = new_result_id


POSTGRES = "postgres"
BLOB = "blob"
# Every tool call is counted and audited in PostgreSQL, so none works without it.
RELIES_ON = {
    POSTGRES: (
        LOOKUP_CUSTOMER,
        GET_ORDER,
        LIST_TICKETS,
        GET_TICKET,
        SEARCH_RUNBOOKS,
        GET_POLICY_DOCUMENT,
        UPDATE_TICKET,
        ISSUE_REFUND,
    ),
    BLOB: (GET_POLICY_DOCUMENT,),
}


def guard_for(backend: str, limits: Limits, *, timeout_s: int, classify: Classify) -> Guard:
    return Guard(
        backend,
        GuardLimits(
            timeout_s=timeout_s,
            retries=limits.retry.max,
            retry_base_ms=limits.retry.base_ms,
            concurrent=limits.bulkhead.per_backend,
        ),
        breaker=CircuitBreaker(
            failures=limits.breaker.failures,
            window_s=limits.breaker.window_s,
            half_open_after_s=limits.breaker.half_open_after_s,
        ),
        classify=classify,
    )


def production_services(
    settings: McpSettings, storage: StorageSettings, limits: Limits, engine: AsyncEngine
) -> OpenServices:
    @asynccontextmanager
    async def open_services() -> AsyncGenerator[Services]:
        runbooks = InMemoryRunbooks.from_directory(settings.runbooks_dir)
        postgres = guard_for(
            POSTGRES, limits, timeout_s=limits.timeout.postgres_s, classify=classify_postgres
        )
        blob = guard_for(BLOB, limits, timeout_s=limits.timeout.blob_s, classify=classify_blob)
        database = Database(engine, postgres)
        tickets = PostgresTickets(database)
        actions = PostgresPendingActions(database)
        key = settings.approval_key_pem()
        signer = NoApprovalSigner() if key is None else LocalApprovalSigner(key)
        async with policies_container(storage, sdk_retries=0) as container:
            policies = BlobPolicies(container, blob, max_bytes=limits.policy.document_bytes)
            yield Services(
                identities=TokenIdentities(),
                rates=OneAtATime(
                    PostgresRateLimiter(
                        database, per_minute=limits.rate.calls_per_minute, burst=limits.rate.burst
                    )
                ),
                audit=PostgresAuditTrail(database),
                customers=CustomerService(PostgresCustomers(database)),
                orders=OrderService(PostgresOrders(database), limits, utc_now),
                tickets=TicketService(tickets, limits, utc_now),
                proposals=ProposalService(
                    tickets,
                    PostgresRefundCases(database),
                    actions,
                    ExecutionService(
                        actions, PostgresExecutions(database), signer, limits, utc_now
                    ),
                    limits,
                    utc_now,
                ),
                approvals=ApprovalService(actions, signer, limits, utc_now),
                refunds=RefundService(PostgresRefundRetries(database), limits.approval, utc_now),
                runbooks=RunbookService(runbooks, limits),
                policies=PolicyService(policies, limits),
                references=ReferenceService(runbooks, limits),
                health=HealthService((postgres, blob), RELIES_ON),
            )

    return open_services

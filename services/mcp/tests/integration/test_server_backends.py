"""The server over the real adapters: PostgreSQL for records, Blob storage for policies."""

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from azure.storage.blob.aio import ContainerClient
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from opscp_common.errors import ErrorCode
from opscp_common.limits import Limits
from opscp_mcp.adapters.blob import BlobPolicies, classify
from opscp_mcp.adapters.local_signer import LocalApprovalSigner
from opscp_mcp.adapters.memory_runbooks import InMemoryRunbooks
from opscp_mcp.adapters.postgres.actions import PostgresPendingActions, PostgresRefundCases
from opscp_mcp.adapters.postgres.audit import PostgresAuditTrail, broken_links
from opscp_mcp.adapters.postgres.execution import PostgresExecutions
from opscp_mcp.adapters.postgres.rate_limits import PostgresRateLimiter
from opscp_mcp.adapters.postgres.refunds import PostgresRefundRetries
from opscp_mcp.authz.identity import Identity
from opscp_mcp.container import Services
from opscp_mcp.main import build_app
from opscp_mcp.services.approvals import ApprovalService
from opscp_mcp.services.customers import CustomerService
from opscp_mcp.services.execution import ExecutionService
from opscp_mcp.services.health import HealthService
from opscp_mcp.services.orders import OrderService
from opscp_mcp.services.policies import PolicyService
from opscp_mcp.services.proposals import ProposalService
from opscp_mcp.services.refunds import RefundService
from opscp_mcp.services.resources import ReferenceService
from opscp_mcp.services.runbooks import RunbookService
from opscp_mcp.services.tickets import TicketService
from opscp_mcp_testing.cases.backend import Backend
from opscp_mcp_testing.guards import a_guard
from opscp_mcp_testing.identities import ARJUN, KAVITA, PRIYA
from opscp_mcp_testing.paths import RUNBOOKS_DIR
from opscp_mcp_testing.server import (
    Caller,
    call,
    connected,
    data_of,
    error_of,
    fake_services,
    server_with,
)
from opscp_mcp_testing.signin import approval_key_pem, dev_sign_in
from opscp_mcp_testing.world import (
    ASHA,
    AWAITING_SUPERVISOR,
    NOW,
    PLAIN,
    SOUTH_ORDER,
    STUCK_ON_ADDRESS,
)

from .database import database_settings, guarded
from .storage import scratch_policies

pytestmark = pytest.mark.integration

_LATEST = text(
    "SELECT target, outcome, error_code, actor_oid, actor_roles, written_by "
    "FROM audit.audit_log ORDER BY id OFFSET :after"
)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def container() -> AsyncGenerator[ContainerClient]:
    async with scratch_policies() as client:
        yield client


type RealServices = Callable[[Identity], Services]


@pytest.fixture
def real(
    backend: Backend, container: ContainerClient, limits: Limits, mcp_app: AsyncEngine
) -> RealServices:
    return lambda identity: _services(identity, backend, container, limits, mcp_app)


def _services(
    identity: Identity,
    backend: Backend,
    container: ContainerClient,
    limits: Limits,
    engine: AsyncEngine,
) -> Services:
    runbooks = InMemoryRunbooks.from_directory(RUNBOOKS_DIR)
    blob = a_guard("blob", classify=classify)
    database = guarded(engine)
    actions = PostgresPendingActions(database)
    signer = LocalApprovalSigner(approval_key_pem())
    policies = BlobPolicies(container, blob, max_bytes=limits.policy.document_bytes)
    return Services(
        identities=Caller(identity),
        rates=PostgresRateLimiter(
            database, per_minute=limits.rate.calls_per_minute, burst=limits.rate.burst
        ),
        audit=PostgresAuditTrail(database),
        customers=CustomerService(backend.customers),
        orders=OrderService(backend.orders, limits, lambda: NOW),
        tickets=TicketService(backend.tickets, limits, lambda: NOW),
        proposals=ProposalService(
            backend.tickets,
            PostgresRefundCases(database),
            actions,
            ExecutionService(actions, PostgresExecutions(database), signer, limits, lambda: NOW),
            limits,
            lambda: NOW,
        ),
        approvals=ApprovalService(actions, signer, limits, lambda: NOW),
        refunds=RefundService(PostgresRefundRetries(database), limits.approval, lambda: NOW),
        runbooks=RunbookService(runbooks, limits),
        policies=PolicyService(policies, limits),
        references=ReferenceService(runbooks, limits),
        health=HealthService((blob,), {"blob": ("get_policy_document",)}),
    )


async def test_the_reference_read_sequence_runs_against_real_backends(
    real: RealServices, limits: Limits, mcp_app: AsyncEngine
) -> None:
    server = server_with(real(PRIYA), limits)
    async with mcp_app.connect() as connection:
        rows_before, _ = await broken_links(connection, limit=1)

    async with connected(server) as priya:
        order = data_of(await call(priya, "get_order", order_id=STUCK_ON_ADDRESS))
        customer = data_of(await call(priya, "lookup_customer", customer_id=order["customer_id"]))
        listed = data_of(await call(priya, "list_tickets", order_id=STUCK_ON_ADDRESS))
        ticket = data_of(await call(priya, "get_ticket", ticket_id=PLAIN))
        runbooks = data_of(
            await call(
                priya, "search_runbooks", query="address validation failed, order stuck in packed"
            )
        )
        policy = data_of(
            await call(priya, "get_policy_document", name="refund-policy", section="goodwill")
        )
        elsewhere = error_of(await call(priya, "get_order", order_id=SOUTH_ORDER))

    assert (order["status"], order["status_age_hours"], order["is_stuck"]) == ("packed", 96, True)
    assert (customer["customer_id"], customer["email"]) == (ASHA, "a***@example.in")
    assert [item["ticket_id"] for item in listed["items"]] == [PLAIN]
    assert ticket["summary"] == "Order stuck for four days"
    assert runbooks["results"][0]["runbook_id"] == "rb-001"
    assert "| Premium | 72 hours | 15% of order value |" in policy["section"]["content"]
    assert elsewhere.code is ErrorCode.NOT_FOUND
    async with mcp_app.connect() as connection:
        recorded = (await connection.execute(_LATEST, {"after": rows_before})).all()
        rows_after, broken = await broken_links(connection, limit=1)
    assert (rows_after - rows_before, broken) == (7, [])
    assert [(row.target, row.outcome, row.error_code) for row in recorded] == [
        ("get_order", "ok", None),
        ("lookup_customer", "ok", None),
        ("list_tickets", "ok", None),
        ("get_ticket", "ok", None),
        ("search_runbooks", "ok", None),
        ("get_policy_document", "ok", None),
        ("get_order", "error", "NOT_FOUND"),
    ]
    assert {(row.actor_oid, tuple(row.actor_roles), row.written_by) for row in recorded} == {
        (PRIYA.oid, ("Support.L1",), "mcp_app")
    }


async def test_what_a_caller_sees_depends_on_who_they_are(
    real: RealServices, limits: Limits
) -> None:
    async with connected(server_with(real(ARJUN), limits)) as arjun:
        unmasked = data_of(await call(arjun, "lookup_customer", customer_id=ASHA))
        as_requester = data_of(await call(arjun, "get_ticket", ticket_id=AWAITING_SUPERVISOR))
    async with connected(server_with(real(KAVITA), limits)) as kavita:
        south = data_of(await call(kavita, "get_order", order_id=SOUTH_ORDER))
        as_supervisor = data_of(await call(kavita, "get_ticket", ticket_id=AWAITING_SUPERVISOR))

    assert unmasked["email"] == "asha.rao@example.in"
    assert south["order_id"] == SOUTH_ORDER
    assert (as_requester["needs_my_approval"], as_supervisor["needs_my_approval"]) == (False, True)


@pytest.mark.usefixtures("world")
def test_the_process_is_ready_when_the_database_answers(limits: Limits) -> None:
    engine = create_async_engine(database_settings("mcp_app").url())

    @asynccontextmanager
    async def open_services() -> AsyncGenerator[Services]:
        yield fake_services(PRIYA, limits)

    with TestClient(build_app(engine, open_services, limits, dev_sign_in())) as http:
        response = http.get("/readyz")

    assert (response.status_code, response.json()) == (200, {"status": "ok"})

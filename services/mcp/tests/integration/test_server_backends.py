"""The server over the real adapters: PostgreSQL for records, Blob storage for policies."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from azure.storage.blob.aio import ContainerClient
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from opscp_common.errors import ErrorCode
from opscp_common.limits import Limits
from opscp_mcp.adapters.blob import BlobPolicies
from opscp_mcp.adapters.memory_runbooks import InMemoryRunbooks
from opscp_mcp.authz.identity import Identity
from opscp_mcp.container import Services
from opscp_mcp.main import build_app
from opscp_mcp.services.customers import CustomerService
from opscp_mcp.services.orders import OrderService
from opscp_mcp.services.policies import PolicyService
from opscp_mcp.services.resources import ReferenceService
from opscp_mcp.services.runbooks import RunbookService
from opscp_mcp.services.tickets import TicketService
from opscp_mcp_testing.cases.backend import Backend
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
from opscp_mcp_testing.world import (
    ASHA,
    AWAITING_SUPERVISOR,
    NOW,
    PLAIN,
    SOUTH_ORDER,
    STUCK_ON_ADDRESS,
)

from .database import database_settings
from .storage import scratch_policies

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def container() -> AsyncGenerator[ContainerClient]:
    async with scratch_policies() as client:
        yield client


def _services(
    identity: Identity, backend: Backend, container: ContainerClient, limits: Limits
) -> Services:
    runbooks = InMemoryRunbooks.from_directory(RUNBOOKS_DIR)
    policies = BlobPolicies(
        container, timeout_s=limits.timeout.blob_s, max_bytes=limits.policy.document_bytes
    )
    return Services(
        identities=Caller(identity),
        customers=CustomerService(backend.customers),
        orders=OrderService(backend.orders, limits, lambda: NOW),
        tickets=TicketService(backend.tickets, limits, lambda: NOW),
        runbooks=RunbookService(runbooks, limits),
        policies=PolicyService(policies, limits),
        references=ReferenceService(runbooks, limits),
    )


async def test_the_reference_read_sequence_runs_against_real_backends(
    backend: Backend, container: ContainerClient, limits: Limits
) -> None:
    server = server_with(_services(PRIYA, backend, container, limits), limits)

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


async def test_what_a_caller_sees_depends_on_who_they_are(
    backend: Backend, container: ContainerClient, limits: Limits
) -> None:
    async with connected(
        server_with(_services(ARJUN, backend, container, limits), limits)
    ) as arjun:
        unmasked = data_of(await call(arjun, "lookup_customer", customer_id=ASHA))
        as_requester = data_of(await call(arjun, "get_ticket", ticket_id=AWAITING_SUPERVISOR))
    async with connected(
        server_with(_services(KAVITA, backend, container, limits), limits)
    ) as kavita:
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

    with TestClient(build_app(engine, open_services, limits)) as http:
        response = http.get("/readyz")

    assert (response.status_code, response.json()) == (200, {"status": "ok"})

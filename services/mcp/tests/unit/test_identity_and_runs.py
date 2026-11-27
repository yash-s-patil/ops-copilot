"""Who the caller is, as the server tells it, and which run a request says it belongs to."""

from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from opscp_common.limits import Limits
from opscp_mcp.authz.identity import Identity
from opscp_mcp.authz.policy import permissions_for
from opscp_mcp.authz.tokens import TokenIdentities
from opscp_mcp.container import Services
from opscp_mcp.main import MCP_PATH, build_app
from opscp_mcp.services.runs import RUN_HEADER, run_named_in
from opscp_mcp_testing.fakes.controls import FakeAuditTrail
from opscp_mcp_testing.identities import ARJUN, KAVITA, PRIYA, ROHAN, SAM
from opscp_mcp_testing.server import fake_services
from opscp_mcp_testing.signin import bearer, dev_sign_in, token_for
from opscp_mcp_testing.world import STUCK_ON_ADDRESS

NOWHERE = "postgresql+asyncpg://nobody:unused@127.0.0.1:1/nothing"
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}
RUN = "3f0e7ec1-5a4b-4c2d-9e8f-0123456789ab"


class _Server:
    def __init__(self, http: TestClient, audit: FakeAuditTrail) -> None:
        self.http = http
        self.audit = audit

    def get_order(self, who: Identity, **headers: str) -> Any:
        call = {"name": "get_order", "arguments": {"order_id": STUCK_ON_ADDRESS}}
        return self.http.post(
            MCP_PATH,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": call},
            headers=MCP_HEADERS | bearer(token_for(who)) | headers,
        )


@pytest.fixture
def server(limits: Limits) -> Iterator[_Server]:
    services = replace(fake_services(PRIYA, limits), identities=TokenIdentities())
    assert isinstance(services.audit, FakeAuditTrail)

    @asynccontextmanager
    async def open_services() -> AsyncGenerator[Services]:
        yield services

    app = build_app(create_async_engine(NOWHERE), open_services, limits, dev_sign_in())
    with TestClient(app) as http:
        yield _Server(http, services.audit)


@pytest.mark.parametrize("who", [PRIYA, ROHAN, ARJUN, KAVITA, SAM])
def test_the_server_tells_a_caller_who_they_are_and_what_their_role_allows(
    server: _Server, who: Identity
) -> None:
    response = server.http.get("/identity", headers=bearer(token_for(who)))

    assert response.status_code == 200
    assert response.json() == {
        "oid": str(who.oid),
        "name": who.name,
        "roles": sorted(role.value for role in who.roles),
        "region": who.region,
        "permissions": sorted(permission.value for permission in permissions_for(who.roles)),
    }


def test_a_person_with_no_role_here_is_told_they_hold_no_permission(server: _Server) -> None:
    nobody = replace_roles(PRIYA)

    response = server.http.get("/identity", headers=bearer(token_for(nobody)))

    assert (response.json()["roles"], response.json()["permissions"]) == ([], [])


def replace_roles(person: Identity) -> Identity:
    return Identity(oid=person.oid, name=person.name, roles=frozenset(), region=person.region)


def test_identity_needs_sign_in_and_is_refused_to_a_web_page(server: _Server) -> None:
    unsigned = server.http.get("/identity")
    from_a_page = server.http.get(
        "/identity", headers=bearer(token_for(PRIYA)) | {"Origin": "https://evil.example"}
    )

    assert unsigned.status_code == 401
    assert "resource_metadata" in unsigned.headers["WWW-Authenticate"]
    assert from_a_page.status_code == 403


def test_a_tool_call_that_names_its_run_is_audited_with_it(server: _Server) -> None:
    server.get_order(PRIYA, **{RUN_HEADER: RUN})
    server.get_order(PRIYA)

    assert [entry.run_id for entry in server.audit.entries] == [RUN, None]


@pytest.mark.parametrize(
    "named",
    ["", "not-a-run", "run-1; DROP TABLE", RUN + "0", "x" * 500, "ananya.k@example.in"],
)
def test_a_run_id_that_is_not_one_is_not_written_down(server: _Server, named: str) -> None:
    response = server.get_order(PRIYA, **{RUN_HEADER: named})

    assert response.status_code == 200
    assert [entry.run_id for entry in server.audit.entries] == [None]


def test_a_decision_made_for_a_run_is_audited_with_it(server: _Server) -> None:
    headers = bearer(token_for(ARJUN)) | {RUN_HEADER: RUN.upper()}

    server.http.post(f"/approvals/{uuid4()}/token", headers=headers)

    assert [(entry.action, entry.run_id) for entry in server.audit.entries] == [("call", RUN)]


def test_the_run_is_read_whatever_the_case_of_its_letters() -> None:
    assert run_named_in({RUN_HEADER: RUN.upper()}) == RUN
    assert run_named_in({}) is None

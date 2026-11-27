"""Approving, rejecting and carrying a token: who may, when, and what is refused."""

import asyncio
import json
from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
import structlog
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import create_async_engine

from opscp_common.limits import Limits
from opscp_mcp.adapters.local_signer import LocalApprovalSigner, NoApprovalSigner
from opscp_mcp.authz.identity import Identity
from opscp_mcp.authz.tokens import TokenIdentities
from opscp_mcp.container import Services
from opscp_mcp.main import build_app
from opscp_mcp.ports.signing import ApprovalSigner
from opscp_mcp.services.approvals import ApprovalService
from opscp_mcp.services.proposals import ProposalService, RefundRequest, TicketUpdate
from opscp_mcp_testing.fakes.actions import FakePendingActions, FakeRefundCases
from opscp_mcp_testing.fakes.controls import FakeAuditTrail, FakeRateLimiter
from opscp_mcp_testing.fakes.meridian import FakeTickets
from opscp_mcp_testing.identities import ARJUN, KAVITA, PRIYA, ROHAN, SAM
from opscp_mcp_testing.server import fake_services
from opscp_mcp_testing.signin import approval_key_pem, bearer, dev_sign_in, token_for
from opscp_mcp_testing.world import LOSS_CONFIRMED, NEVER_SCANNED, NOW, PLAIN, WORLD

NOWHERE = "postgresql+asyncpg://nobody:unused@127.0.0.1:1/nothing"
SMALL = RefundRequest(
    order_id=NEVER_SCANNED,
    amount_minor=50000,
    reason_code="goodwill_delay",
    reason_text="No carrier scan for 80 hours.",
)
LARGE = RefundRequest(
    order_id=LOSS_CONFIRMED,
    amount_minor=890000,
    reason_code="lost_in_transit",
    reason_text="The carrier confirmed the loss.",
)
OTHER_KAVITA = Identity(
    oid=UUID("0f1e2d3c-0006-4000-8000-000000000006"),
    name="Meena Pillai",
    roles=KAVITA.roles,
    region="*",
)


class _Office:
    """The server over HTTP, with a clock and stores the test can reach."""

    def __init__(self, limits: Limits, signer: ApprovalSigner | None = None) -> None:
        self.limits = limits
        self.now = NOW
        self.audit = FakeAuditTrail()
        self.actions = FakePendingActions(self.audit)
        self.signer = signer or LocalApprovalSigner(approval_key_pem())
        self.rates: Any = None
        self.http: TestClient

    def services(self) -> Services:
        base = fake_services(ARJUN, self.limits)
        return replace(
            base,
            identities=TokenIdentities(),
            audit=self.audit,
            rates=self.rates or base.rates,
            proposals=ProposalService(
                FakeTickets(WORLD),
                FakeRefundCases(WORLD, self.actions),
                self.actions,
                self.limits,
                lambda: self.now,
            ),
            approvals=ApprovalService(self.actions, self.signer, self.limits, lambda: self.now),
        )

    async def asked(self, identity: Identity, request: RefundRequest = SMALL) -> dict[str, str]:
        proposal = await self.services().proposals.refund(
            identity, request, idempotency_key=uuid4().hex
        )
        return {"id": str(proposal.action_id), "hash": proposal.args_hash}

    def post(self, identity: Identity | None, path: str, body: Any = None) -> Any:
        headers = {} if identity is None else bearer(token_for(identity))
        return self.http.post(path, json=body, headers=headers)

    def get(self, identity: Identity, path: str) -> Any:
        return self.http.get(path, headers=bearer(token_for(identity)))

    def approve(self, identity: Identity, action: dict[str, str], **changed: str) -> Any:
        body = {"args_hash": action["hash"]} | changed
        return self.post(identity, f"/approvals/{action['id']}/approve", body)

    def reject(
        self, identity: Identity, action: dict[str, str], reason: str = "Not needed."
    ) -> Any:
        return self.post(identity, f"/approvals/{action['id']}/reject", {"reason": reason})

    def token(self, identity: Identity, action: dict[str, str]) -> Any:
        return self.post(identity, f"/approvals/{action['id']}/token")

    def status_of(self, action: dict[str, str]) -> str:
        return next(a.status for a in self.actions.stored if str(a.id) == action["id"])


@pytest.fixture
def office(limits: Limits) -> Iterator[_Office]:
    yield from _opened(_Office(limits))


def _opened(office: _Office) -> Iterator[_Office]:
    @asynccontextmanager
    async def open_services() -> AsyncGenerator[Services]:
        yield office.services()

    app = build_app(create_async_engine(NOWHERE), open_services, office.limits, dev_sign_in())
    with TestClient(app) as http:
        office.http = http
        yield office


def _code(response: Any) -> tuple[int, str]:
    return response.status_code, response.json()["code"]


async def test_the_requester_confirms_a_small_refund_and_is_given_a_token(office: _Office) -> None:
    action = await office.asked(ARJUN)

    response = office.approve(ARJUN, action)

    approved = response.json()
    claims = await office.signer.verify(approved["approval"]["token"])
    assert response.status_code == 200
    assert (approved["action"]["status"], approved["action"]["approver_name"]) == (
        "approved",
        "Arjun Mehta",
    )
    assert claims is not None
    assert claims.model_dump(exclude={"token_id"}) == {
        "requester_oid": ARJUN.oid,
        "approver_oid": ARJUN.oid,
        "action_id": UUID(action["id"]),
        "tool": "issue_refund",
        "args_hash": action["hash"],
        "issued_at": NOW,
        "expires_at": NOW + timedelta(seconds=120),
    }
    assert approved["approval"]["expires_at"] == "2026-11-28T04:32:00Z"
    assert approved["action"]["expires_at"] == "2026-11-29T04:30:00Z"


async def test_an_approval_of_anything_but_what_was_shown_is_refused(office: _Office) -> None:
    action = await office.asked(ARJUN)

    response = office.approve(ARJUN, action, args_hash="0" * 64)

    assert _code(response) == (409, "APPROVAL_INVALID")
    assert office.status_of(action) == "proposed"


@pytest.mark.parametrize("who", [PRIYA, ROHAN, KAVITA, SAM], ids=lambda who: who.name)
async def test_nobody_else_can_see_or_confirm_a_small_refund(
    office: _Office, who: Identity
) -> None:
    action = await office.asked(ARJUN)

    assert _code(office.approve(who, action)) == (404, "NOT_FOUND")
    assert _code(office.reject(who, action)) == (404, "NOT_FOUND")
    assert _code(office.token(who, action)) == (404, "NOT_FOUND")
    assert _code(office.get(who, f"/approvals/{action['id']}")) == (404, "NOT_FOUND")
    assert office.status_of(action) == "proposed"


async def test_an_action_that_does_not_exist_looks_the_same_as_one_that_is_hidden(
    office: _Office,
) -> None:
    hidden = office.approve(PRIYA, await office.asked(ARJUN))
    missing = office.approve(PRIYA, {"id": str(uuid4()), "hash": "0" * 64})

    assert hidden.json() == missing.json()


async def test_a_large_refund_cannot_be_approved_by_the_person_who_asked(office: _Office) -> None:
    by_engineer = await office.asked(ARJUN, LARGE)

    assert _code(office.approve(ARJUN, by_engineer)) == (403, "PERMISSION_DENIED")
    assert office.status_of(by_engineer) == "proposed"


async def test_a_supervisor_cannot_approve_their_own_large_refund(office: _Office) -> None:
    by_supervisor = await office.asked(KAVITA, LARGE)

    own = office.approve(KAVITA, by_supervisor)
    another = office.approve(OTHER_KAVITA, by_supervisor)

    assert _code(own) == (403, "PERMISSION_DENIED")
    assert another.status_code == 200
    assert another.json()["approval"] is None


async def test_a_supervisors_approval_gives_nobody_a_token_until_the_requester_asks(
    office: _Office,
) -> None:
    action = await office.asked(ARJUN, LARGE)

    approved = office.approve(KAVITA, action).json()
    for_supervisor = office.token(KAVITA, action)
    for_requester = office.token(ARJUN, action)

    claims = await office.signer.verify(for_requester.json()["token"])
    assert (approved["approval"], approved["action"]["status"]) == (None, "approved")
    assert _code(for_supervisor) == (403, "PERMISSION_DENIED")
    assert claims is not None
    assert (claims.requester_oid, claims.approver_oid) == (ARJUN.oid, KAVITA.oid)


async def test_each_token_asked_for_is_a_new_one(office: _Office) -> None:
    action = await office.asked(ARJUN)
    first = office.approve(ARJUN, action).json()["approval"]["token"]

    second = office.token(ARJUN, action).json()["token"]
    third = office.token(ARJUN, action).json()["token"]

    assert len({first, second, third}) == 3


async def test_a_token_is_given_only_for_an_approved_action(office: _Office) -> None:
    proposed = await office.asked(ARJUN)
    rejected = await office.asked(ARJUN, LARGE)
    office.reject(KAVITA, rejected)

    assert _code(office.token(ARJUN, proposed)) == (409, "APPROVAL_INVALID")
    assert _code(office.token(ARJUN, rejected)) == (409, "APPROVAL_INVALID")


async def test_an_action_is_decided_once(office: _Office) -> None:
    action = await office.asked(ARJUN, LARGE)
    office.approve(KAVITA, action)

    assert _code(office.approve(KAVITA, action)) == (409, "APPROVAL_INVALID")
    assert _code(office.reject(KAVITA, action)) == (409, "APPROVAL_INVALID")
    assert _code(office.reject(ARJUN, action)) == (409, "APPROVAL_INVALID")
    assert office.status_of(action) == "approved"


async def test_an_action_past_its_time_cannot_be_decided_and_shows_as_expired(
    office: _Office,
) -> None:
    action = await office.asked(ARJUN)
    office.now = NOW + timedelta(minutes=15)

    shown = office.get(ARJUN, f"/approvals/{action['id']}").json()

    assert _code(office.approve(ARJUN, action)) == (409, "APPROVAL_INVALID")
    assert _code(office.reject(ARJUN, action)) == (409, "APPROVAL_INVALID")
    assert (shown["status"], shown["may_decide"]) == ("expired", False)


async def test_an_approval_lapses_a_day_after_it_was_given(office: _Office) -> None:
    action = await office.asked(ARJUN)
    office.approve(ARJUN, action)

    office.now = NOW + timedelta(hours=23, minutes=59)
    in_time = office.token(ARJUN, action)
    office.now = NOW + timedelta(hours=24)
    too_late = office.token(ARJUN, action)

    assert in_time.status_code == 200
    assert _code(too_late) == (409, "APPROVAL_INVALID")


async def test_someone_who_lost_the_right_to_ask_can_neither_confirm_nor_carry_it_out(
    office: _Office,
) -> None:
    demoted = replace_roles(ARJUN, PRIYA)
    confirmed = await office.asked(ARJUN)
    waiting = await office.asked(ARJUN, LARGE)
    office.approve(KAVITA, waiting)

    assert _code(office.approve(demoted, confirmed)) == (403, "PERMISSION_DENIED")
    assert _code(office.token(demoted, waiting)) == (403, "PERMISSION_DENIED")
    assert office.status_of(confirmed) == "proposed"


def replace_roles(person: Identity, like: Identity) -> Identity:
    return person.model_copy(update={"roles": like.roles})


async def test_a_supervisor_refuses_with_a_reason_and_the_requester_may_withdraw(
    office: _Office,
) -> None:
    refused = await office.asked(ARJUN, LARGE)
    withdrawn = await office.asked(ARJUN)

    by_supervisor = office.reject(KAVITA, refused, "  The loss is not confirmed in writing.  ")
    by_requester = office.reject(ARJUN, withdrawn, "Asked for the wrong order.")

    assert (by_supervisor.status_code, by_requester.status_code) == (200, 200)
    assert by_supervisor.json()["status"] == by_requester.json()["status"] == "rejected"
    assert by_supervisor.json()["approver_name"] == "Kavita Rao"
    stored = {str(action.id): action.reason for action in office.actions.stored}
    assert stored[refused["id"]] == "The loss is not confirmed in writing."


async def test_a_rejected_request_no_longer_blocks_a_new_one(office: _Office) -> None:
    first = await office.asked(ARJUN)
    office.reject(ARJUN, first)

    second = await office.asked(ARJUN)

    assert second["id"] != first["id"]


BAD_BODIES: dict[str, tuple[str, Any]] = {
    "no fingerprint": ("approve", {}),
    "a fingerprint of the wrong length": ("approve", {"args_hash": "abc"}),
    "a fingerprint in capitals": ("approve", {"args_hash": "A" * 64}),
    "something extra": ("approve", {"args_hash": "0" * 64, "force": True}),
    "no body": ("approve", None),
    "no reason": ("reject", {}),
    "an empty reason": ("reject", {"reason": ""}),
    "a reason of nothing readable": ("reject", {"reason": " ‮ "}),
    "a reason far too long": ("reject", {"reason": "x" * 2001}),
    "a reason over the limit": ("reject", {"reason": "x" * 301}),
}


@pytest.mark.parametrize(("route", "body"), BAD_BODIES.values(), ids=BAD_BODIES.keys())
async def test_a_malformed_decision_is_refused_without_repeating_what_was_sent(
    office: _Office, route: str, body: Any
) -> None:
    action = await office.asked(ARJUN)

    response = office.post(ARJUN, f"/approvals/{action['id']}/{route}", body)

    assert _code(response) == (422, "VALIDATION_FAILED")
    assert set(response.json()) == {"code", "message", "retryable", "details"}
    assert "xxxx" not in response.text
    assert office.status_of(action) == "proposed"


@pytest.mark.parametrize("action_id", ["1", "not-an-id", "asha.rao@example.in"])
def test_an_id_that_is_not_one_is_refused_without_being_repeated(
    office: _Office, action_id: str
) -> None:
    response = office.post(ARJUN, f"/approvals/{action_id}/approve", {"args_hash": "0" * 64})

    assert _code(response) == (422, "VALIDATION_FAILED")
    assert action_id not in response.text


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/approvals"),
        ("GET", "/approvals/{id}"),
        ("POST", "/approvals/{id}/approve"),
        ("POST", "/approvals/{id}/reject"),
        ("POST", "/approvals/{id}/token"),
    ],
)
async def test_every_route_needs_sign_in_and_says_where_to_get_it(
    office: _Office, method: str, path: str
) -> None:
    action = await office.asked(ARJUN)
    url = path.replace("{id}", action["id"])

    unsigned = office.http.request(method, url, json={"args_hash": action["hash"]})
    bad = office.http.request(
        method, url, json={"args_hash": action["hash"]}, headers=bearer("not-a-token")
    )

    for response in (unsigned, bad):
        assert response.status_code == 401
        assert "oauth-protected-resource/mcp" in response.headers["www-authenticate"]
        assert response.text == ""
    assert office.status_of(action) == "proposed"


async def test_a_web_page_cannot_approve_even_with_a_good_token(office: _Office) -> None:
    action = await office.asked(ARJUN)

    response = office.http.post(
        f"/approvals/{action['id']}/approve",
        json={"args_hash": action["hash"]},
        headers=bearer(token_for(ARJUN)) | {"Origin": "https://evil.example"},
    )

    assert response.status_code == 403
    assert office.status_of(action) == "proposed"


async def test_a_person_with_no_role_is_refused(office: _Office) -> None:
    nobody = ARJUN.model_copy(update={"roles": frozenset()})
    action = await office.asked(ARJUN)

    assert _code(office.get(nobody, "/approvals")) == (403, "PERMISSION_DENIED")
    assert _code(office.approve(nobody, action)) == (403, "PERMISSION_DENIED")


async def test_the_list_shows_what_you_asked_for_and_what_waits_for_you(office: _Office) -> None:
    small = await office.asked(ARJUN)
    large = await office.asked(ARJUN, LARGE)

    def listed(who: Identity, query: str = "") -> list[tuple[str, bool, bool]]:
        items = office.get(who, f"/approvals{query}").json()["items"]
        return [(item["action_id"], item["mine"], item["may_decide"]) for item in items]

    assert sorted(listed(ARJUN)) == sorted([(small["id"], True, True), (large["id"], True, False)])
    assert listed(KAVITA) == [(large["id"], False, True)]
    assert listed(PRIYA) == listed(ROHAN) == listed(SAM) == []
    office.approve(KAVITA, large)
    assert listed(KAVITA) == []
    assert (large["id"], True, False) in listed(ARJUN)


async def test_the_list_can_be_narrowed_to_one_ticket_and_is_capped(office: _Office) -> None:
    update = TicketUpdate(ticket_id=PLAIN, note="Called the customer.")
    proposals = office.services().proposals
    for _ in range(30):
        await proposals.ticket_update(PRIYA, update, idempotency_key=uuid4().hex)
    await office.asked(ARJUN)

    everything = office.get(PRIYA, "/approvals").json()["items"]
    on_ticket = office.get(PRIYA, f"/approvals?ticket_id={PLAIN}").json()["items"]
    elsewhere = office.get(ARJUN, f"/approvals?ticket_id={PLAIN}").json()["items"]

    assert (len(everything), len(on_ticket), elsewhere) == (25, 25, [])
    assert _code(office.get(PRIYA, "/approvals?ticket_id=0")) == (422, "VALIDATION_FAILED")


async def test_the_detail_shows_exactly_what_would_be_done(office: _Office) -> None:
    action = await office.asked(ARJUN, LARGE)

    shown = office.get(KAVITA, f"/approvals/{action['id']}").json()

    assert shown["args"] == LARGE.model_dump(exclude_none=True)
    assert shown["args_hash"] == action["hash"]
    assert shown["policy_snapshot"]["cap_minor"] == 890000
    assert (shown["summary"], shown["requester_name"], shown["approval_kind"]) == (
        f"Refund INR 8,900.00 on order {LOSS_CONFIRMED} (lost_in_transit)",
        "Arjun Mehta",
        "supervisor",
    )
    assert (shown["mine"], shown["may_decide"], shown["status"]) == (False, True, "proposed")


async def test_every_decision_is_audited_and_no_token_is_recorded_or_logged(
    office: _Office,
) -> None:
    small = await office.asked(ARJUN)
    large = await office.asked(ARJUN, LARGE)

    with structlog.testing.capture_logs() as logs:
        token = office.approve(ARJUN, small).json()["approval"]["token"]
        office.approve(ARJUN, large)
        office.reject(KAVITA, large, "Write to asha.rao@example.in first.")
        office.approve(ARJUN, small, args_hash="0" * 64)
        fresh = office.token(ARJUN, small).json()["token"]

    recorded = [
        (entry.target, entry.action, entry.outcome, entry.error_code, entry.actor_oid)
        for entry in office.audit.entries
    ]
    assert recorded == [
        ("POST /approvals/{id}/approve", "approve", "ok", None, ARJUN.oid),
        ("POST /approvals/{id}/approve", "deny", "denied", "PERMISSION_DENIED", ARJUN.oid),
        ("POST /approvals/{id}/reject", "reject", "ok", None, KAVITA.oid),
        ("POST /approvals/{id}/approve", "approve", "error", "APPROVAL_INVALID", ARJUN.oid),
        ("POST /approvals/{id}/token", "call", "ok", None, ARJUN.oid),
    ]
    everything = json.dumps(logs, default=str) + "".join(
        entry.model_dump_json() for entry in office.audit.entries
    )
    assert office.audit.entries[0].args_redacted == {
        "action_id": small["id"],
        "args_hash": small["hash"],
    }
    assert "asha.rao" not in everything
    for secret in (token, fresh):
        assert secret not in everything
        assert secret.split(".")[2] not in everything


def test_decisions_count_against_the_rate_limit(limits: Limits) -> None:
    limited = _Office(limits)
    limited.rates = FakeRateLimiter(per_minute=60, burst=2, clock=lambda: NOW)

    for office in _opened(limited):
        first, second, third = (office.get(ARJUN, "/approvals") for _ in range(3))

        assert (first.status_code, second.status_code) == (200, 200)
        assert _code(third) == (429, "RATE_LIMITED")
        assert third.headers["retry-after"] == "1"


async def test_when_approvals_cannot_be_signed_the_action_stays_as_it_was(limits: Limits) -> None:
    for office in _opened(_Office(limits, NoApprovalSigner())):
        small = await office.asked(ARJUN)
        large = await office.asked(ARJUN, LARGE)

        own = office.approve(ARJUN, small)
        by_supervisor = office.approve(KAVITA, large)
        later = office.token(ARJUN, large)

        assert _code(own) == (503, "BACKEND_UNAVAILABLE")
        assert office.status_of(small) == "proposed"
        assert by_supervisor.status_code == 200
        assert _code(later) == (503, "BACKEND_UNAVAILABLE")
        assert office.status_of(large) == "approved"


async def test_a_defect_while_deciding_is_reported_as_internal_without_its_text(
    limits: Limits,
) -> None:
    class _Broken:
        async def sign(self, claims: object) -> str:
            raise RuntimeError("key for asha.rao@example.in is corrupt")

        async def verify(self, token: str) -> None:
            return None

    for office in _opened(_Office(limits, _Broken())):
        action = await office.asked(ARJUN)

        response = office.approve(ARJUN, action)

        assert _code(response) == (500, "INTERNAL")
        assert "asha" not in response.text
        assert office.audit.entries[-1].error_code == "INTERNAL"


async def test_a_defect_is_answered_inside_the_application_so_the_web_server_logs_nothing(
    limits: Limits, caplog: pytest.LogCaptureFixture
) -> None:
    """The test client raises anything that escapes the application; nothing may."""

    class _Corrupt:
        async def sign(self, claims: object) -> str:
            raise ValidationError.from_exception_data("ActionRecord", [])

        async def verify(self, token: str) -> None:
            return None

    for office in _opened(_Office(limits, _Corrupt())):
        with structlog.testing.capture_logs() as logs:
            response = office.approve(ARJUN, await office.asked(ARJUN))

        assert _code(response) == (500, "INTERNAL")
        assert [line["event"] for line in logs if line["log_level"] == "error"] == [
            "request_failed"
        ]
        assert "Traceback" not in caplog.text


async def test_an_approval_and_its_audit_entry_are_stored_together_or_not_at_all(
    limits: Limits,
) -> None:
    class _Down:
        async def record(self, entry: object) -> int:
            raise ConnectionError("audit database down")

    office = _Office(limits)
    office.actions = FakePendingActions(_Down())
    for opened in _opened(office):
        action = await opened.asked(ARJUN)

        with structlog.testing.capture_logs() as logs:
            response = opened.approve(ARJUN, action)

        assert _code(response) == (500, "INTERNAL")
        assert opened.status_of(action) == "proposed"
        assert "approval" not in response.text
        assert [line["event"] for line in logs] == ["audit", "request_failed"]
        (failure,) = opened.audit.entries
        assert (failure.action, failure.outcome, failure.error_code) == (
            "approve",
            "error",
            "INTERNAL",
        )


async def test_a_token_never_outlives_the_approval_it_carries(office: _Office) -> None:
    action = await office.asked(ARJUN)
    office.approve(ARJUN, action)
    office.now = NOW + timedelta(hours=24) - timedelta(seconds=30)

    token = office.token(ARJUN, action).json()

    claims = await office.signer.verify(token["token"])
    assert claims is not None
    assert claims.expires_at == NOW + timedelta(hours=24)
    assert token["expires_at"] == "2026-11-29T04:30:00Z"


async def test_a_supervisor_no_longer_sees_a_request_that_was_withdrawn_or_has_expired(
    office: _Office,
) -> None:
    withdrawn = await office.asked(ARJUN, LARGE)
    office.reject(ARJUN, withdrawn, "Asked for the wrong order.")
    lapsed = await office.asked(ARJUN, LARGE)
    office.now = NOW + timedelta(hours=24)

    for action in (withdrawn, lapsed):
        assert _code(office.get(KAVITA, f"/approvals/{action['id']}")) == (404, "NOT_FOUND")
    shown = office.get(ARJUN, f"/approvals/{withdrawn['id']}").json()
    assert (shown["status"], shown["decision_reason"]) == ("rejected", "Asked for the wrong order.")


async def test_the_requester_is_told_why_a_supervisor_refused(office: _Office) -> None:
    action = await office.asked(ARJUN, LARGE)
    office.reject(KAVITA, action, "The loss is not confirmed in writing.")

    shown = office.get(ARJUN, f"/approvals/{action['id']}").json()

    assert (shown["status"], shown["approver_name"], shown["decision_reason"]) == (
        "rejected",
        "Kavita Rao",
        "The loss is not confirmed in writing.",
    )


@pytest.mark.parametrize("ticket", ["9" * 40, str(2**63), "-1", "abc", "1.5"])
def test_a_ticket_number_that_cannot_be_one_is_refused(office: _Office, ticket: str) -> None:
    assert _code(office.get(ARJUN, f"/approvals?ticket_id={ticket}")) == (422, "VALIDATION_FAILED")


async def test_a_body_larger_than_a_decision_needs_is_refused_before_it_is_read(
    office: _Office,
) -> None:
    action = await office.asked(ARJUN)
    url = f"/approvals/{action['id']}/reject"

    too_large = office.http.post(url, content=b"x" * 5000, headers=bearer(token_for(ARJUN)))
    unsigned = office.http.post(url, content=b"x" * 5000)
    no_length = office.http.post(
        url, content=iter([b'{"reason":', b'"x"}']), headers=bearer(token_for(ARJUN))
    )

    assert _code(too_large) == _code(unsigned) == (413, "VALIDATION_FAILED")
    assert _code(no_length) == (411, "VALIDATION_FAILED")
    assert office.status_of(action) == "proposed"


async def test_a_request_with_no_body_at_all_needs_no_length(office: _Office) -> None:
    action = await office.asked(ARJUN)
    office.approve(ARJUN, action)
    url = f"/approvals/{action['id']}/token"
    bare = office.http.build_request("POST", url, headers=bearer(token_for(ARJUN)))
    del bare.headers["content-length"]

    response = office.http.send(bare)

    assert "content-length" not in bare.headers
    assert response.status_code == 200


async def test_a_body_that_is_not_json_is_refused_without_being_repeated(office: _Office) -> None:
    action = await office.asked(ARJUN)

    response = office.http.post(
        f"/approvals/{action['id']}/approve",
        content=b'{"args_hash": <script>',
        headers=bearer(token_for(ARJUN)) | {"Content-Type": "application/json"},
    )

    assert response.status_code in (400, 422)
    assert response.json()["code"] == "VALIDATION_FAILED"
    assert "script" not in response.text


async def test_a_decision_that_takes_too_long_is_answered_as_a_timeout(limits: Limits) -> None:
    class _Slow:
        async def sign(self, claims: object) -> str:
            await asyncio.sleep(3600)
            raise AssertionError

        async def verify(self, token: str) -> None:
            return None

    quick = limits.model_copy(
        update={"approval": limits.approval.model_copy(update={"route_timeout_s": 0})}
    )
    for office in _opened(_Office(quick, _Slow())):
        action = await office.asked(ARJUN)

        response = office.approve(ARJUN, action)

        assert _code(response) == (504, "TIMEOUT")
        assert office.status_of(action) == "proposed"

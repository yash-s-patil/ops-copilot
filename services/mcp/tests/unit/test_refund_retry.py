"""Retry payment: who may send a failed refund round again, and when it is refused."""

from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from opscp_common.errors import PolicyViolationError
from opscp_common.limits import Limits
from opscp_common.vocabulary import PAYMENT_OUTCOME_UNKNOWN
from opscp_mcp.authz.identity import Identity
from opscp_mcp.authz.tokens import TokenIdentities
from opscp_mcp.container import Services
from opscp_mcp.main import build_app
from opscp_mcp.ports.actions import RefundCase
from opscp_mcp.ports.refunds import RefundToRetry
from opscp_mcp.services.refunds import may_retry
from opscp_mcp.services.scope import scope_of
from opscp_mcp_testing.fakes.controls import FakeAuditTrail
from opscp_mcp_testing.identities import ARJUN, KAVITA, PRIYA, ROHAN, SAM
from opscp_mcp_testing.server import connected, fake_services, fake_writes, server_with
from opscp_mcp_testing.signin import bearer, dev_sign_in, token_for
from opscp_mcp_testing.world import LOSS_CONFIRMED, NOW, SOUTH_ORDER, WORLD, RefundRow, ago

NOWHERE = "postgresql+asyncpg://nobody:unused@127.0.0.1:1/nothing"
FAILED, PAID, SOUTH_FAILED, UNKNOWN = 900401, 900402, 900403, 900499
STUCK_GOODWILL = 900302
ORDER_VALUE = 890000
_ACTIONS = iter(UUID(int=number) for number in range(7000, 7100))


def _refund(refund_id: int, status: str, **changed: Any) -> RefundRow:
    facts: dict[str, Any] = {
        "id": refund_id,
        "order_id": LOSS_CONFIRMED,
        "region": "west",
        "amount_minor": 100000,
        "status": status,
        "pending_action_id": next(_ACTIONS),
        "reason_code": "lost_in_transit",
        "failure_reason": "provider_declined" if status == "failed" else None,
    }
    return RefundRow(**(facts | changed))


_WORLD = replace(
    WORLD,
    refunds=(
        *WORLD.refunds,
        _refund(FAILED, "failed"),
        _refund(PAID, "paid"),
        _refund(SOUTH_FAILED, "failed", order_id=SOUTH_ORDER, region="south", amount_minor=1000),
    ),
)


def _case(**changed: Any) -> RefundCase:
    facts: dict[str, Any] = {
        "order_id": LOSS_CONFIRMED,
        "region": "west",
        "tier": "standard",
        "status": "lost",
        "status_changed_at": ago(100),
        "amount_minor": ORDER_VALUE,
        "refunded_minor": 0,
        "last_new_scan_at": None,
        "carrier_exception_at": None,
        "loss_confirmed": True,
        "damage_reported": False,
        "goodwill_given": False,
        "request_waiting": False,
    }
    return RefundCase.model_validate(facts | changed)


def _to_retry(**changed: Any) -> RefundToRetry:
    facts: dict[str, Any] = {
        "id": FAILED,
        "order_id": LOSS_CONFIRMED,
        "amount_minor": 100000,
        "reason_code": "lost_in_transit",
        "status": "failed",
        "self_approved": True,
        "outcome_unknown": False,
    }
    return RefundToRetry.model_validate(facts | changed)


def _rule(refund: RefundToRetry, case: RefundCase, limits: Limits) -> dict[str, Any]:
    with pytest.raises(PolicyViolationError) as refused:
        may_retry(limits.approval, refund, case)
    return refused.value.body().details


def test_a_failed_refund_that_still_fits_may_be_sent_again(limits: Limits) -> None:
    may_retry(limits.approval, _to_retry(), _case(refunded_minor=300000))


@pytest.mark.parametrize("status", ["approved", "sent_to_payments", "paid"])
def test_a_refund_that_did_not_fail_is_not_sent_again(status: str, limits: Limits) -> None:
    assert _rule(_to_retry(status=status), _case(), limits) == {"rule": "refund_not_failed"}


def test_a_refund_that_no_longer_fits_in_the_order_is_refused(limits: Limits) -> None:
    made_since = _case(refunded_minor=ORDER_VALUE - 99999)

    assert _rule(_to_retry(self_approved=False), made_since, limits) == {
        "rule": "exceeds_remaining",
        "cap_minor": 99999,
        "requested_minor": 100000,
    }


def test_a_goodwill_refund_is_not_sent_again_once_another_was_made(limits: Limits) -> None:
    goodwill = _to_retry(reason_code="goodwill_delay")

    assert _rule(goodwill, _case(goodwill_given=True), limits) == {"rule": "goodwill_already_given"}
    may_retry(limits.approval, goodwill, _case())
    may_retry(limits.approval, _to_retry(), _case(goodwill_given=True))


def test_a_refund_is_not_sent_again_while_another_request_on_the_order_waits(
    limits: Limits,
) -> None:
    assert _rule(_to_retry(), _case(request_waiting=True), limits) == {
        "rule": "request_waiting",
        "cap_minor": 0,
        "requested_minor": 100000,
    }


def test_a_self_approved_refund_cannot_be_retried_past_the_limit_a_supervisor_guards(
    limits: Limits,
) -> None:
    """Invariant 5: failing and retrying must not be a way to split a refund under the limit."""
    limit = limits.approval.self_limit_minor
    just_over = _case(refunded_minor=limit - 100000 + 1)

    assert _rule(_to_retry(), just_over, limits) == {"rule": "needs_supervisor"}
    may_retry(limits.approval, _to_retry(), _case(refunded_minor=limit - 100000))
    may_retry(limits.approval, _to_retry(self_approved=False), just_over)


def test_a_refund_of_unknown_outcome_is_never_left_with_no_way_forward(limits: Limits) -> None:
    """It counted all along, so a later refund was approved knowing of it. It cannot be asked
    for again while it counts; refusing the retry too would strand it."""
    limit = limits.approval.self_limit_minor
    approved_since_by_a_supervisor = _case(refunded_minor=limit)

    may_retry(limits.approval, _to_retry(outcome_unknown=True), approved_since_by_a_supervisor)


async def test_a_refund_whose_payment_outcome_is_unknown_still_counts_against_its_order(
    limits: Limits,
) -> None:
    """The provider may yet pay it, so the order has no room for a second refund in its place."""
    unknown = _refund(900410, "failed", failure_reason=PAYMENT_OUTCOME_UNKNOWN)
    declined = _refund(900411, "failed", failure_reason="provider_declined")
    world = replace(WORLD, refunds=(unknown, declined))
    cases = fake_writes(world, limits, FakeAuditTrail()).executions.cases

    case = await cases.for_order(scope_of(ARJUN), LOSS_CONFIRMED, now=NOW)
    without_it = await cases.for_order(
        scope_of(ARJUN), LOSS_CONFIRMED, now=NOW, leaving_out=unknown.id
    )

    assert case is not None
    assert without_it is not None
    assert (case.refunded_minor, without_it.refunded_minor) == (100000, 0)


class _Desk:
    """The server over HTTP on in-memory stores the test can look into."""

    def __init__(self, limits: Limits) -> None:
        self.limits = limits
        self.audit = FakeAuditTrail()
        self.writes = fake_writes(_WORLD, limits, self.audit)
        self.http: TestClient

    def services(self) -> Services:
        return replace(
            fake_services(ARJUN, self.limits, _WORLD),
            identities=TokenIdentities(),
            audit=self.audit,
            refunds=self.writes.refunds,
        )

    def retry(self, identity: Identity | None, refund_id: object, **sent: Any) -> Any:
        headers = {} if identity is None else bearer(token_for(identity))
        return self.http.post(f"/refunds/{refund_id}/retry", headers=headers, **sent)

    def last_audit(self) -> tuple[str, str, str | None]:
        entry = self.audit.entries[-1]
        return entry.action, entry.outcome, entry.error_code


@pytest.fixture
def desk(limits: Limits) -> Iterator[_Desk]:
    desk = _Desk(limits)

    @asynccontextmanager
    async def open_services() -> AsyncGenerator[Services]:
        yield desk.services()

    app = build_app(create_async_engine(NOWHERE), open_services, limits, dev_sign_in())
    with TestClient(app) as http:
        desk.http = http
        yield desk


def _refused(response: Any) -> tuple[int, str, Any]:
    body = response.json()
    return response.status_code, body["code"], body["details"].get("rule")


def test_an_l2_engineer_sends_a_failed_refund_to_payments_again(desk: _Desk) -> None:
    response = desk.retry(ARJUN, FAILED)

    assert (response.status_code, response.json()) == (
        200,
        {"refund_id": FAILED, "status": "approved"},
    )
    assert (desk.writes.retries.status_of(FAILED), desk.writes.retries.queued) == (
        "approved",
        [FAILED],
    )
    entry = desk.audit.entries[-1]
    assert (entry.action, entry.outcome, entry.target, entry.args_redacted) == (
        "retry",
        "ok",
        "POST /refunds/{id}/retry",
        {"refund_id": FAILED},
    )


def test_a_supervisor_reaches_refunds_in_every_region(desk: _Desk) -> None:
    """The south order has a request waiting, so the refund is seen and then refused."""
    assert _refused(desk.retry(KAVITA, SOUTH_FAILED)) == (
        409,
        "POLICY_VIOLATION",
        "request_waiting",
    )
    assert desk.retry(KAVITA, FAILED).status_code == 200
    assert desk.writes.retries.queued == [FAILED]


def test_retrying_twice_queues_one_payment(desk: _Desk) -> None:
    desk.retry(ARJUN, FAILED)

    assert _refused(desk.retry(ARJUN, FAILED)) == (409, "POLICY_VIOLATION", "refund_not_failed")
    assert desk.writes.retries.queued == [FAILED]
    assert desk.last_audit() == ("retry", "error", "POLICY_VIOLATION")


def test_a_refund_that_was_paid_is_not_sent_again(desk: _Desk) -> None:
    assert _refused(desk.retry(ARJUN, PAID)) == (409, "POLICY_VIOLATION", "refund_not_failed")
    assert desk.writes.retries.queued == []


def test_a_goodwill_refund_overtaken_by_another_is_not_sent_again(desk: _Desk) -> None:
    refused = desk.retry(ARJUN, STUCK_GOODWILL)

    assert _refused(refused) == (409, "POLICY_VIOLATION", "goodwill_already_given")
    assert desk.writes.retries.status_of(STUCK_GOODWILL) == "failed"


@pytest.mark.parametrize("who", [PRIYA, SAM])
def test_without_the_permission_to_ask_for_refunds_nobody_retries(
    desk: _Desk, who: Identity
) -> None:
    response = desk.retry(who, FAILED)

    assert (response.status_code, response.json()["code"]) == (403, "PERMISSION_DENIED")
    assert desk.writes.retries.queued == []
    assert desk.last_audit() == ("deny", "denied", "PERMISSION_DENIED")


def test_a_refund_in_another_region_looks_like_one_that_does_not_exist(desk: _Desk) -> None:
    other_region, missing = desk.retry(ARJUN, SOUTH_FAILED), desk.retry(ARJUN, UNKNOWN)

    assert other_region.status_code == missing.status_code == 404
    assert other_region.json() == missing.json()
    assert desk.writes.retries.queued == []


def test_an_l1_engineer_of_the_region_is_refused_before_anything_is_looked_up(
    desk: _Desk,
) -> None:
    assert desk.retry(ROHAN, UNKNOWN).json()["code"] == "PERMISSION_DENIED"


def test_nobody_retries_without_signing_in(desk: _Desk) -> None:
    response = desk.retry(None, FAILED)

    assert response.status_code == 401
    assert "resource_metadata" in response.headers["WWW-Authenticate"]
    assert desk.audit.entries == []


@pytest.mark.parametrize("refund_id", ["abc", "0", "-4", "1.5", str(2**53)])
def test_a_number_that_cannot_be_a_refund_is_refused_without_being_repeated(
    desk: _Desk, refund_id: str
) -> None:
    response = desk.retry(ARJUN, refund_id)

    assert (response.status_code, response.json()["code"]) == (422, "VALIDATION_FAILED")
    assert "abc" not in response.text
    assert desk.audit.entries == []


def test_a_large_body_is_refused_before_it_is_read(desk: _Desk) -> None:
    too_much = "x" * (desk.limits.approval.body_bytes_max + 1)

    assert desk.retry(ARJUN, FAILED, content=too_much).status_code == 413
    assert desk.writes.retries.queued == []


def test_a_web_page_cannot_retry_for_the_person_looking_at_it(desk: _Desk) -> None:
    headers = bearer(token_for(ARJUN)) | {"Origin": "https://example.org"}

    response = desk.http.post(f"/refunds/{FAILED}/retry", headers=headers)

    assert response.status_code == 403
    assert desk.writes.retries.queued == []


def test_nothing_is_queued_when_the_record_of_it_cannot_be_written(desk: _Desk) -> None:
    desk.audit.down = True

    response = desk.retry(ARJUN, FAILED)

    assert response.status_code == 500
    assert (desk.writes.retries.status_of(FAILED), desk.writes.retries.queued) == ("failed", [])


async def test_no_tool_retries_a_payment(limits: Limits) -> None:
    """Invariant 13: retry is a route behind a button, never something the model can call."""
    for identity in (PRIYA, ARJUN, KAVITA, SAM):
        async with connected(server_with(fake_services(identity, limits), limits)) as client:
            names = {tool.name for tool in await client.list_tools()}
        assert not any("retry" in name or "pay" in name for name in names)

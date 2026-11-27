"""Pending actions and refund facts against PostgreSQL."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from opscp_common.errors import PolicyViolationError
from opscp_common.hashing import JsonValue, args_hash
from opscp_common.limits import Limits
from opscp_mcp.adapters.postgres.actions import PostgresPendingActions, PostgresRefundCases
from opscp_mcp.adapters.postgres.tickets import PostgresTickets
from opscp_mcp.authz.identity import Identity
from opscp_mcp.ports.actions import NewAction
from opscp_mcp.services.proposals import ProposalService, RefundRequest, TicketUpdate
from opscp_mcp.services.scope import scope_of
from opscp_mcp_testing.fakes.actions import FakePendingActions, FakeRefundCases
from opscp_mcp_testing.identities import ARJUN, KAVITA, PRIYA, ROHAN
from opscp_mcp_testing.world import (
    AWAITING_SUPERVISOR,
    LOSS_CONFIRMED,
    NEVER_SCANNED,
    NO_ORDER,
    NOW,
    PAID,
    PAYMENT_FAILED,
    PLAIN,
    SENT_TO_PAYMENTS,
    WORLD,
)

from .database import guarded

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("world")]

_BUSINESS_ROWS = text(
    "SELECT (SELECT count(*) FROM core.refunds), (SELECT count(*) FROM control.outbox),"
    " (SELECT count(*) FROM core.ticket_notes),"
    " (SELECT md5(string_agg(id::text || status || queue || version::text, ',' ORDER BY id))"
    " FROM core.tickets)"
)
_STORED = text(
    "SELECT tool, args, args_hash, requester_oid, requester_name, requester_region,"
    " approval_kind, status, policy_snapshot, idempotency_key, ticket_id, order_id, expires_at,"
    " approver_oid, executed_at, result FROM control.pending_actions WHERE id = :id"
)
_LIVE_ON_ORDER = text(
    "SELECT count(*) FROM control.pending_actions WHERE order_id = :order_id"
    " AND status = 'proposed' AND idempotency_key LIKE 'it-%'"
)
_OURS = text("DELETE FROM control.pending_actions WHERE idempotency_key LIKE 'it-%'")


@pytest.fixture(autouse=True)
async def forget_what_the_tests_proposed(migrator: AsyncEngine) -> None:
    async with migrator.begin() as connection:
        await connection.execute(_OURS)


def _service(engine: AsyncEngine, limits: Limits) -> ProposalService:
    database = guarded(engine)
    return ProposalService(
        PostgresTickets(database),
        PostgresRefundCases(database),
        PostgresPendingActions(database),
        limits,
        lambda: NOW,
    )


def _new(key: str, **changed: object) -> NewAction:
    args: dict[str, JsonValue] = {
        "order_id": NEVER_SCANNED,
        "amount_minor": 50000,
        "reason_code": "goodwill_delay",
    }
    return NewAction.model_validate(
        {
            "tool": "issue_refund",
            "args": args,
            "args_hash": args_hash("issue_refund", args),
            "requester_oid": ARJUN.oid,
            "requester_name": ARJUN.name,
            "requester_region": "west",
            "approval_kind": "self",
            "policy_snapshot": {"cap_minor": 93000},
            "idempotency_key": key,
            "ticket_id": None,
            "order_id": NEVER_SCANNED,
            "expires_at": NOW + timedelta(minutes=15),
        }
        | changed
    )


@pytest.mark.parametrize("identity", [ARJUN, ROHAN, KAVITA], ids=lambda identity: identity.name)
async def test_the_facts_a_refund_is_judged_on_are_the_same_as_the_fakes_give(
    mcp_app: AsyncEngine, identity: Identity
) -> None:
    real = PostgresRefundCases(guarded(mcp_app))
    fake = FakeRefundCases(WORLD, FakePendingActions())
    scope = scope_of(identity)

    for order in (*(row.id for row in WORLD.orders), NO_ORDER):
        assert await real.for_order(scope, order, now=NOW) == await fake.for_order(
            scope, order, now=NOW
        )


async def test_a_request_is_stored_exactly_as_it_was_made(mcp_app: AsyncEngine) -> None:
    new = _new("it-stored", ticket_id=PLAIN)

    stored = await PostgresPendingActions(guarded(mcp_app)).propose(new, now=NOW)
    assert stored is not None

    async with mcp_app.connect() as connection:
        row = (await connection.execute(_STORED, {"id": stored.id})).mappings().one()
    assert {name: row[name] for name in new.model_dump()} == new.model_dump()
    assert (row["status"], row["approver_oid"], row["executed_at"], row["result"]) == (
        "proposed",
        None,
        None,
        None,
    )
    assert stored.model_dump(exclude={"id"}) == {
        "tool": "issue_refund",
        "args": new.args,
        "args_hash": new.args_hash,
        "requester_oid": ARJUN.oid,
        "approval_kind": "self",
        "status": "proposed",
        "expires_at": new.expires_at,
    }


async def test_the_same_key_finds_the_same_action_and_only_for_its_requester(
    mcp_app: AsyncEngine,
) -> None:
    actions = PostgresPendingActions(guarded(mcp_app))
    stored = await actions.propose(_new("it-find"), now=NOW)

    assert await actions.find(scope_of(ARJUN), "issue_refund", "it-find") == stored
    assert await actions.find(scope_of(ARJUN), "update_ticket", "it-find") is None
    assert await actions.find(scope_of(KAVITA), "issue_refund", "it-find") is None
    assert await actions.find(scope_of(ARJUN), "issue_refund", "it-other") is None


async def test_many_requests_with_one_key_at_the_same_moment_store_one_action(
    mcp_app: AsyncEngine,
) -> None:
    actions = PostgresPendingActions(guarded(mcp_app))

    stored = await asyncio.gather(*(actions.propose(_new("it-race"), now=NOW) for _ in range(12)))

    assert len({None if action is None else action.id for action in stored}) == 1
    assert None not in stored


async def test_a_key_already_used_keeps_its_first_arguments(mcp_app: AsyncEngine) -> None:
    actions = PostgresPendingActions(guarded(mcp_app))
    first = await actions.propose(_new("it-keep"), now=NOW)

    second = await actions.propose(
        _new("it-keep", args={"order_id": 1}, args_hash="other"), now=NOW
    )

    assert second == first


async def test_a_waiting_request_shows_in_the_facts_until_it_expires(mcp_app: AsyncEngine) -> None:
    database = guarded(mcp_app)
    cases, actions = PostgresRefundCases(database), PostgresPendingActions(database)
    scope = scope_of(ARJUN)
    before = await cases.for_order(scope, NEVER_SCANNED, now=NOW)

    await actions.propose(_new("it-waiting"), now=NOW)
    during = await cases.for_order(scope, NEVER_SCANNED, now=NOW)
    elsewhere = await cases.for_order(scope, LOSS_CONFIRMED, now=NOW)
    later = await cases.for_order(scope, NEVER_SCANNED, now=NOW + timedelta(minutes=15))

    assert before is not None
    assert during is not None
    assert elsewhere is not None
    assert later is not None
    assert [before.request_waiting, during.request_waiting, later.request_waiting] == [
        False,
        True,
        False,
    ]
    assert elsewhere.request_waiting is False


async def test_asking_for_writes_changes_no_business_row(
    mcp_app: AsyncEngine, limits: Limits
) -> None:
    service = _service(mcp_app, limits)
    async with mcp_app.connect() as connection:
        before = (await connection.execute(_BUSINESS_ROWS)).one()

    refund = await service.refund(
        ARJUN,
        RefundRequest(
            order_id=NEVER_SCANNED,
            amount_minor=50000,
            reason_code="goodwill_delay",
            reason_text="No carrier scan for 80 hours.",
        ),
        idempotency_key="it-refund",
    )
    again = await service.refund(
        ARJUN,
        RefundRequest(
            order_id=NEVER_SCANNED,
            amount_minor=50000,
            reason_code="goodwill_delay",
            reason_text="No carrier scan for 80 hours.",
        ),
        idempotency_key="it-refund",
    )
    update = await service.ticket_update(
        PRIYA,
        TicketUpdate(ticket_id=PLAIN, status="resolved", note="Sorted with the customer."),
        idempotency_key=f"it-{uuid4().hex}",
    )

    async with mcp_app.connect() as connection:
        after = (await connection.execute(_BUSINESS_ROWS)).one()
    assert after == before
    assert (refund.status, update.status) == ("approval_required", "approval_required")
    assert again == refund


async def test_requests_on_one_order_at_the_same_moment_with_different_keys_store_one(
    mcp_app: AsyncEngine, limits: Limits
) -> None:
    service = _service(mcp_app, limits)
    request = RefundRequest(
        order_id=NEVER_SCANNED,
        amount_minor=50000,
        reason_code="goodwill_delay",
        reason_text="No carrier scan for 80 hours.",
    )

    answers = await asyncio.gather(
        *(
            service.refund(
                ARJUN if number % 2 else KAVITA, request, idempotency_key=f"it-r{number}"
            )
            for number in range(8)
        ),
        return_exceptions=True,
    )

    taken = [answer for answer in answers if not isinstance(answer, BaseException)]
    refused = [answer for answer in answers if isinstance(answer, PolicyViolationError)]
    assert (len(taken), len(refused)) == (1, 7)
    assert [error.body().details["rule"] for error in refused] == ["request_waiting"] * 7
    async with mcp_app.connect() as connection:
        live = (await connection.execute(_LIVE_ON_ORDER, {"order_id": NEVER_SCANNED})).scalar_one()
    assert live == 1


async def test_a_refund_request_blocks_resolving_its_ticket_until_it_expires(
    mcp_app: AsyncEngine,
) -> None:
    database = guarded(mcp_app)
    cases, actions = PostgresRefundCases(database), PostgresPendingActions(database)
    scope = scope_of(ARJUN)
    before = await cases.unfinished_on_ticket(scope, PLAIN, now=NOW)

    await actions.propose(_new("it-ticket", ticket_id=PLAIN), now=NOW)

    assert before is False
    assert await cases.unfinished_on_ticket(scope, PLAIN, now=NOW) is True
    assert await cases.unfinished_on_ticket(scope, PLAIN, now=NOW + timedelta(minutes=15)) is False


@pytest.mark.parametrize(
    ("ticket", "unfinished"),
    [(SENT_TO_PAYMENTS, True), (AWAITING_SUPERVISOR, True), (PAYMENT_FAILED, False), (PAID, False)],
)
async def test_an_unsettled_refund_or_a_waiting_request_counts_as_unfinished(
    mcp_app: AsyncEngine, ticket: int, unfinished: bool
) -> None:
    real = PostgresRefundCases(guarded(mcp_app))
    fake = FakeRefundCases(WORLD, FakePendingActions())
    scope = scope_of(ARJUN)

    assert await real.unfinished_on_ticket(scope, ticket, now=NOW) is unfinished
    assert await fake.unfinished_on_ticket(scope, ticket, now=NOW) is unfinished


async def test_a_refund_request_must_name_its_order(migrator: AsyncEngine) -> None:
    orderless = text(
        "INSERT INTO control.pending_actions (tool, args, args_hash, requester_oid,"
        " requester_name, requester_region, approval_kind, idempotency_key, expires_at)"
        " VALUES ('issue_refund', '{}', 'h', gen_random_uuid(), 'x', 'west', 'self', 'it-none',"
        " now())"
    )

    async with migrator.connect() as connection:
        with pytest.raises(DBAPIError, match="pending_actions_refund_order_check"):
            await connection.execute(orderless)

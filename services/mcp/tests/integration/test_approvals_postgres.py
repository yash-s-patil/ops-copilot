"""Deciding and listing pending actions against PostgreSQL."""

import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from opscp_common.hashing import JsonValue, args_hash
from opscp_mcp.adapters.postgres.actions import PostgresPendingActions
from opscp_mcp.adapters.postgres.audit import broken_links
from opscp_mcp.authz.identity import Identity
from opscp_mcp.ports.actions import ActionRecord, ActionsWanted, Decision, NewAction
from opscp_mcp.ports.controls import AuditEntry
from opscp_mcp_testing.identities import ARJUN, KAVITA, PRIYA
from opscp_mcp_testing.world import NOW, PLAIN

from .database import guarded

ARGS: dict[str, JsonValue] = {"ticket_id": PLAIN, "note": "A note."}
HASH = args_hash("update_ticket", ARGS)
ENTRY = AuditEntry(
    actor_oid=KAVITA.oid,
    actor_roles=("Support.Supervisor",),
    actor_kind="user",
    target="POST /approvals/{id}/approve",
    action="approve",
    args_redacted={},
    outcome="ok",
    duration_ms=1,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("world")]

_OURS = text("DELETE FROM control.pending_actions WHERE idempotency_key LIKE 'ap-%'")
_LAST_AUDIT = text("SELECT id, action, actor_oid FROM audit.audit_log ORDER BY id DESC LIMIT 1")
_STORED = text(
    "SELECT status, approver_oid, approver_name, decision_reason, decided_at, expires_at"
    " FROM control.pending_actions WHERE id = :id"
)


@pytest.fixture(autouse=True)
async def forget_what_the_tests_proposed(migrator: AsyncEngine) -> None:
    async with migrator.begin() as connection:
        await connection.execute(_OURS)


async def _proposed(
    actions: PostgresPendingActions, *, kind: str = "supervisor", **changed: object
) -> ActionRecord:
    """A ticket update, so that several can wait at once: refunds wait one to an order."""
    new = NewAction.model_validate(
        {
            "tool": "update_ticket",
            "args": ARGS,
            "args_hash": HASH,
            "requester_oid": ARJUN.oid,
            "requester_name": ARJUN.name,
            "requester_region": "west",
            "approval_kind": kind,
            "policy_snapshot": {"ticket_version": 1},
            "idempotency_key": f"ap-{uuid4().hex}",
            "ticket_id": None,
            "order_id": None,
            "expires_at": NOW + timedelta(hours=24),
        }
        | changed
    )
    stored = await actions.propose(new, now=NOW)
    assert stored is not None
    return stored


def _decision(status: str, by: Identity = KAVITA, **changed: object) -> Decision:
    return Decision.model_validate(
        {
            "status": status,
            "seen_hash": HASH,
            "approver_oid": by.oid,
            "approver_name": by.name,
            "reason": None if status == "approved" else "Not justified.",
            "decided_at": NOW,
            "expires_at": NOW + timedelta(hours=24),
        }
        | changed
    )


async def test_an_action_is_read_back_by_its_id(mcp_app: AsyncEngine) -> None:
    actions = PostgresPendingActions(guarded(mcp_app))
    stored = await _proposed(actions, ticket_id=PLAIN)

    assert await actions.get(stored.id) == stored
    assert await actions.get(UUID(int=1)) is None
    assert (stored.ticket_id, stored.approver_oid, stored.decided_at) == (PLAIN, None, None)


async def test_a_decision_is_stored_with_who_made_it_and_why(mcp_app: AsyncEngine) -> None:
    actions = PostgresPendingActions(guarded(mcp_app))
    stored = await _proposed(actions)

    rejected = await actions.decide(stored.id, _decision("rejected"), ENTRY)

    async with mcp_app.connect() as connection:
        row = (await connection.execute(_STORED, {"id": stored.id})).mappings().one()
    assert rejected is not None
    assert (
        rejected.action.status,
        rejected.action.approver_oid,
        rejected.action.decision_reason,
    ) == (
        "rejected",
        KAVITA.oid,
        "Not justified.",
    )
    assert dict(row) == {
        "status": "rejected",
        "approver_oid": KAVITA.oid,
        "approver_name": "Kavita Rao",
        "decision_reason": "Not justified.",
        "decided_at": NOW,
        "expires_at": NOW + timedelta(hours=24),
    }


async def test_of_many_decisions_at_the_same_moment_exactly_one_is_taken(
    mcp_app: AsyncEngine,
) -> None:
    actions = PostgresPendingActions(guarded(mcp_app))
    stored = await _proposed(actions)
    attempts = [_decision("approved" if number % 2 else "rejected") for number in range(10)]

    outcomes = await asyncio.gather(*(actions.decide(stored.id, each, ENTRY) for each in attempts))

    taken = [outcome for outcome in outcomes if outcome is not None]
    assert len(taken) == 1
    assert await actions.get(stored.id) == taken[0].action


async def test_an_action_past_its_expiry_or_already_decided_is_left_alone(
    mcp_app: AsyncEngine,
) -> None:
    actions = PostgresPendingActions(guarded(mcp_app))
    lapsed = await _proposed(actions, expires_at=NOW)
    decided = await _proposed(actions)
    await actions.decide(decided.id, _decision("approved"), ENTRY)

    assert await actions.decide(lapsed.id, _decision("approved"), ENTRY) is None
    assert await actions.decide(decided.id, _decision("rejected"), ENTRY) is None
    assert await actions.decide(UUID(int=1), _decision("approved"), ENTRY) is None
    assert (await actions.get(lapsed.id)) == lapsed
    after = await actions.get(decided.id)
    assert after is not None
    assert after.status == "approved"


async def test_waiting_actions_are_listed_for_their_requester_and_for_who_may_decide(
    mcp_app: AsyncEngine,
) -> None:
    actions = PostgresPendingActions(guarded(mcp_app))
    for_supervisor = await _proposed(actions, ticket_id=PLAIN)
    own = await _proposed(actions, kind="self")
    approved = await _proposed(actions)
    await actions.decide(approved.id, _decision("approved"), ENTRY)
    rejected = await _proposed(actions)
    await actions.decide(rejected.id, _decision("rejected"), ENTRY)
    await _proposed(actions, expires_at=NOW)

    async def ids(**wanted: object) -> set[UUID]:
        request = ActionsWanted.model_validate(
            {"requester_oid": PRIYA.oid, "awaiting_supervisor_in": None, "ticket_id": None}
            | {"limit": 200}
            | wanted
        )
        found = await actions.waiting(request, now=NOW)
        return {action.id for action in found} & {
            for_supervisor.id,
            own.id,
            approved.id,
            rejected.id,
        }

    assert await ids(requester_oid=ARJUN.oid) == {for_supervisor.id, own.id, approved.id}
    assert await ids() == set()
    assert await ids(awaiting_supervisor_in="*") == {for_supervisor.id}
    assert await ids(awaiting_supervisor_in="west") == {for_supervisor.id}
    assert await ids(awaiting_supervisor_in="south") == set()
    assert await ids(requester_oid=ARJUN.oid, ticket_id=PLAIN) == {for_supervisor.id}


async def test_the_list_is_newest_first_and_capped(mcp_app: AsyncEngine) -> None:
    actions = PostgresPendingActions(guarded(mcp_app))
    made = [await _proposed(actions, kind="self") for _ in range(4)]
    wanted = ActionsWanted(
        requester_oid=ARJUN.oid, awaiting_supervisor_in=None, ticket_id=None, limit=3
    )

    found = await actions.waiting(wanted, now=NOW)

    assert len(found) == 3
    assert [action.created_at for action in found] == sorted(
        (action.created_at for action in found), reverse=True
    )
    assert made[0].id not in {action.id for action in found}


async def test_a_decision_and_its_audit_entry_are_stored_together_or_not_at_all(
    mcp_app: AsyncEngine,
) -> None:
    actions = PostgresPendingActions(guarded(mcp_app))
    taken = await _proposed(actions)
    lapsed = await _proposed(actions, expires_at=NOW)
    async with mcp_app.connect() as connection:
        before, _ = await broken_links(connection, limit=1)

    decided = await actions.decide(taken.id, _decision("approved"), ENTRY)
    refused = await actions.decide(lapsed.id, _decision("approved"), ENTRY)

    async with mcp_app.connect() as connection:
        after, broken = await broken_links(connection, limit=1)
        last = (await connection.execute(_LAST_AUDIT)).mappings().one()
    assert decided is not None
    assert refused is None
    assert (after - before, broken) == (1, [])
    assert (last["id"], last["action"], last["actor_oid"]) == (
        decided.audit_id,
        "approve",
        KAVITA.oid,
    )


async def test_a_decision_made_on_another_fingerprint_is_not_taken(mcp_app: AsyncEngine) -> None:
    actions = PostgresPendingActions(guarded(mcp_app))
    stored = await _proposed(actions)

    elsewhere = await actions.decide(stored.id, _decision("approved", seen_hash="0" * 64), ENTRY)

    assert elsewhere is None
    assert await actions.get(stored.id) == stored


@pytest.mark.parametrize(
    "change",
    [
        "args = '{}'",
        "args_hash = 'other'",
        "requester_oid = gen_random_uuid()",
        "approval_kind = 'self'",
        "tool = 'update_ticket'",
        "order_id = 1",
    ],
)
async def test_the_server_cannot_change_what_was_asked_for(
    mcp_app: AsyncEngine, change: str
) -> None:
    stored = await _proposed(PostgresPendingActions(guarded(mcp_app)))
    statement = text(f"UPDATE control.pending_actions SET {change} WHERE id = :id")  # noqa: S608 -- fixed test text

    async with mcp_app.connect() as connection:
        with pytest.raises(DBAPIError, match="permission denied"):
            await connection.execute(statement, {"id": stored.id})

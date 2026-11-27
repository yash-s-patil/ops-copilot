"""Carrying out an approved action in PostgreSQL, in one transaction."""

import json
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from opscp_common.errors import OpsError
from opscp_common.hashing import JsonValue
from opscp_mcp.adapters.postgres.actions import (
    LOCK_ONE_ORDER,
    lock_key,
    refund_case,
    refund_unfinished_on,
)
from opscp_mcp.adapters.postgres.audit import append
from opscp_mcp.adapters.postgres.outbox import Payment, queue_payment
from opscp_mcp.adapters.postgres.session import SET_SCOPE, Database
from opscp_mcp.ports.actions import RefundCase
from opscp_mcp.ports.execution import (
    ApprovedAction,
    Claim,
    EntryFor,
    Executed,
    NewNote,
    NewRefund,
    RefusedAtExecutionError,
    TicketChange,
    TicketState,
    Work,
    is_refusal_by_the_rules,
)

_NOTE_AUTHOR_KIND = "agent_on_behalf"
_TAKE = text(
    """
    UPDATE control.pending_actions
    SET status = 'executed', executed_at = :now
    WHERE id = :action_id
      AND status = 'approved'
      AND expires_at > :now
      AND args_hash = :args_hash
      AND requester_oid = :requester_oid
      AND approver_oid = :approver_oid
    RETURNING id, tool, args, approval_kind, requester_oid, requester_name, approver_oid,
              approver_name, policy_snapshot
    """
)
_USE_TOKEN = text(
    "INSERT INTO control.approval_token_uses (jti, pending_action_id, used_at) "
    "VALUES (:token_id, :action_id, :now)"
)
_KEEP_RESULT = text(
    "UPDATE control.pending_actions SET result = CAST(:result AS jsonb) WHERE id = :action_id"
)
_MARK_FAILED = text(
    """
    UPDATE control.pending_actions
    SET status = 'failed', executed_at = NULL, result = CAST(:result AS jsonb)
    WHERE id = :action_id
    """
)
_TICKET = text(
    "SELECT id, order_id, region, status, queue, version FROM core.tickets "
    "WHERE id = :ticket_id FOR UPDATE"
)
_ADD_REFUND = text(
    """
    INSERT INTO core.refunds
        (order_id, ticket_id, region, amount_minor, reason_code, reason_text,
         requested_by_oid, requested_by_name, approved_by_oid, approved_by_name,
         pending_action_id, created_at, updated_at)
    VALUES
        (:order_id, :ticket_id, :region, :amount_minor, :reason_code, :reason_text,
         :requested_by_oid, :requested_by_name, :approved_by_oid, :approved_by_name,
         :action_id, :now, :now)
    RETURNING id, currency
    """
)
_CHANGE_TICKET = text(
    """
    UPDATE core.tickets
    SET status = COALESCE(:status, status),
        queue = CASE WHEN :escalating THEN 'L2' ELSE queue END,
        escalated_by_oid = CASE WHEN :escalating THEN :by_oid ELSE escalated_by_oid END,
        escalated_by_name = CASE WHEN :escalating THEN :by_name ELSE escalated_by_name END,
        escalated_at = CASE WHEN :escalating THEN :now ELSE escalated_at END,
        recommendation = CASE WHEN :escalating THEN CAST(:recommendation AS jsonb)
                              ELSE recommendation END,
        version = version + 1,
        updated_at = :now
    WHERE id = :ticket_id AND version = :from_version
    RETURNING id, order_id, region, status, queue, version
    """
)
_ADD_NOTE = text(
    "INSERT INTO core.ticket_notes (ticket_id, author_kind, author_oid, author_name, body, "
    "created_at) VALUES (:ticket_id, :author_kind, :author_oid, :author_name, :body, :now)"
)


class _TokenAlreadyUsedError(Exception):
    """Ends the transaction with nothing changed: the token cannot carry a second write."""


class _Ledger:
    def __init__(self, connection: AsyncConnection, action: ApprovedAction, now: datetime) -> None:
        self._connection = connection
        self._action = action
        self._now = now

    async def refund_case(self, order_id: int) -> RefundCase | None:
        return await refund_case(self._connection, order_id, self._now)

    async def ticket(self, ticket_id: int) -> TicketState | None:
        found = await self._connection.execute(_TICKET, {"ticket_id": ticket_id})
        row = found.mappings().one_or_none()
        return None if row is None else TicketState.model_validate(dict(row))

    async def refund_unfinished_on(self, ticket_id: int) -> bool:
        return await refund_unfinished_on(self._connection, ticket_id, self._now)

    async def add_refund(self, refund: NewRefund) -> int:
        action = self._action
        written = await self._connection.execute(
            _ADD_REFUND,
            refund.model_dump()
            | {
                "requested_by_oid": action.requester_oid,
                "requested_by_name": action.requester_name,
                "approved_by_oid": action.approver_oid,
                "approved_by_name": action.approver_name,
                "action_id": action.id,
                "now": self._now,
            },
        )
        row = written.mappings().one()
        payment = Payment(
            order_id=refund.order_id,
            amount_minor=refund.amount_minor,
            currency=row["currency"].strip(),
            reason_code=refund.reason_code,
        )
        await queue_payment(self._connection, row["id"], payment, self._now)
        return row["id"]

    async def change_ticket(self, change: TicketChange) -> TicketState | None:
        escalation = change.escalation
        advice = None if escalation is None else escalation.recommendation
        changed = await self._connection.execute(
            _CHANGE_TICKET,
            {
                "ticket_id": change.ticket_id,
                "from_version": change.from_version,
                "status": change.status,
                "escalating": escalation is not None,
                "by_oid": None if escalation is None else escalation.by_oid,
                "by_name": None if escalation is None else escalation.by_name,
                "recommendation": None if advice is None else json.dumps(advice),
                "now": self._now,
            },
        )
        row = changed.mappings().one_or_none()
        return None if row is None else TicketState.model_validate(dict(row))

    async def add_note(self, note: NewNote) -> None:
        await self._connection.execute(
            _ADD_NOTE, note.model_dump() | {"author_kind": _NOTE_AUTHOR_KIND, "now": self._now}
        )


class PostgresExecutions:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def run(self, claim: Claim, work: Work, entry: EntryFor) -> Executed | None:
        refused: list[RefusedAtExecutionError] = []

        async def carry_out(connection: AsyncConnection) -> Executed | None:
            action = await _claimed(connection, claim)
            if action is None:
                return None
            try:
                async with connection.begin_nested():
                    result = await work(action, _Ledger(connection, action, claim.now))
            except OpsError as refusal:
                if not is_refusal_by_the_rules(refusal):
                    raise
                audit_id = await _fail(connection, claim, refusal, entry)
                refused.append(RefusedAtExecutionError(refusal, audit_id))
                return None
            kept = {"action_id": claim.action_id, "result": json.dumps(result)}
            await connection.execute(_KEEP_RESULT, kept)
            return Executed(result=result, audit_id=await append(connection, entry(None)))

        try:
            executed = await self._database.write(carry_out)
        except _TokenAlreadyUsedError:
            return None
        if refused:
            raise refused[0]
        return executed


async def _claimed(connection: AsyncConnection, claim: Claim) -> ApprovedAction | None:
    """Takes the action for this caller and uses up the token, or returns None.

    The scope is set first, so that everything read and written afterwards is the caller's.
    A token id that was used before ends the transaction with nothing changed.
    """
    scope = {"user_oid": str(claim.scope.user_oid), "region": claim.scope.region}
    await connection.execute(SET_SCOPE, scope)
    if claim.order_id is not None:
        await connection.execute(LOCK_ONE_ORDER, {"order": lock_key(claim.order_id)})
    taken = await connection.execute(
        _TAKE,
        {
            "action_id": claim.action_id,
            "now": claim.now,
            "args_hash": claim.args_hash,
            "requester_oid": claim.scope.user_oid,
            "approver_oid": claim.approver_oid,
        },
    )
    row = taken.mappings().one_or_none()
    if row is None:
        return None
    used = {"token_id": claim.token_id, "action_id": claim.action_id, "now": claim.now}
    try:
        async with connection.begin_nested():
            await connection.execute(_USE_TOKEN, used)
    except IntegrityError as error:
        raise _TokenAlreadyUsedError from error
    return ApprovedAction.model_validate(dict(row))


async def _fail(
    connection: AsyncConnection, claim: Claim, refusal: OpsError, entry: EntryFor
) -> int:
    body = refusal.body()
    outcome: dict[str, JsonValue] = {"code": body.code.value, "details": body.details}
    await connection.execute(
        _MARK_FAILED, {"action_id": claim.action_id, "result": json.dumps(outcome)}
    )
    return await append(connection, entry(refusal))

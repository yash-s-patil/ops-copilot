from datetime import datetime
from uuid import UUID

import structlog
from pydantic import ValidationError
from sqlalchemy import (
    BigInteger,
    ColumnElement,
    DateTime,
    RowMapping,
    Select,
    and_,
    exists,
    func,
    literal,
    or_,
    select,
    true,
    tuple_,
)
from sqlalchemy.ext.asyncio import AsyncConnection

from opscp_common.vocabulary import (
    ISSUE_REFUND,
    REFUND_SENT,
    REQUEST_APPROVED,
    REQUEST_PROPOSED,
    SUPERVISOR_APPROVAL,
)
from opscp_mcp.adapters.postgres.session import Database
from opscp_mcp.adapters.postgres.tables import (
    customers,
    pending_actions,
    refunds,
    ticket_notes,
    tickets,
)
from opscp_mcp.ports.meridian import (
    NoteRecord,
    RecommendationRecord,
    RefundArguments,
    RefundRecord,
    RefundRequestRecord,
    Scope,
    TicketFilter,
    TicketPageRecord,
    TicketPageRequest,
    TicketPosition,
    TicketRecord,
    TicketWithNotes,
)

_LOG = structlog.get_logger(__name__)

_NEWEST_UPDATE_FIRST = (tickets.c.updated_at.desc(), tickets.c.id.desc())
_SUPERVISOR_REFUND_REQUEST = and_(
    pending_actions.c.ticket_id == tickets.c.id,
    pending_actions.c.tool == ISSUE_REFUND,
    pending_actions.c.approval_kind == SUPERVISOR_APPROVAL,
)
_REFUND_SENT_TO_PAYMENTS = exists().where(
    refunds.c.ticket_id == tickets.c.id, refunds.c.status == REFUND_SENT
)


def _is_proposed(now: datetime) -> ColumnElement[bool]:
    return and_(pending_actions.c.status == REQUEST_PROPOSED, pending_actions.c.expires_at > now)


def _awaits_supervisor(
    now: datetime, *, not_requested_by: UUID | None = None
) -> ColumnElement[bool]:
    request = exists().where(_SUPERVISOR_REFUND_REQUEST, _is_proposed(now))
    if not_requested_by is not None:
        request = request.where(pending_actions.c.requester_oid != not_requested_by)
    return request


def _conditions(matching: TicketFilter, now: datetime) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    if matching.statuses is not None:
        conditions.append(tickets.c.status.in_(matching.statuses))
    for column, wanted in (
        (tickets.c.queue, matching.queue),
        (tickets.c.order_id, matching.order_id),
        (tickets.c.customer_id, matching.customer_id),
        (tickets.c.escalated_by_oid, matching.escalated_by),
    ):
        if wanted is not None:
            conditions.append(column == wanted)
    if matching.refund_in_flight is not None:
        in_flight = or_(_awaits_supervisor(now), _REFUND_SENT_TO_PAYMENTS)
        conditions.append(in_flight if matching.refund_in_flight else ~in_flight)
    if matching.awaiting_approval_not_requested_by is not None:
        conditions.append(
            _awaits_supervisor(now, not_requested_by=matching.awaiting_approval_not_requested_by)
        )
    return conditions


def _tickets(scope: Scope, now: datetime) -> Select[tuple[object, ...]]:
    for_the_caller_to_decide = and_(
        pending_actions.c.status == REQUEST_PROPOSED,
        pending_actions.c.requester_oid != scope.user_oid,
    )
    request = (
        select(
            pending_actions.c.id,
            pending_actions.c.status,
            pending_actions.c.requester_oid,
            pending_actions.c.requester_name,
            pending_actions.c.approver_name,
            pending_actions.c.args,
        )
        .where(
            _SUPERVISOR_REFUND_REQUEST,
            pending_actions.c.status.in_((REQUEST_PROPOSED, REQUEST_APPROVED)),
            pending_actions.c.expires_at > now,
        )
        .order_by(
            for_the_caller_to_decide.desc(),
            pending_actions.c.created_at.desc(),
            pending_actions.c.id.desc(),
        )
        .limit(1)
        .lateral("request")
    )
    refund = (
        select(refunds)
        .where(refunds.c.ticket_id == tickets.c.id)
        .order_by(refunds.c.created_at.desc(), refunds.c.id.desc())
        .limit(1)
        .lateral("refund")
    )
    return (
        select(
            tickets,
            customers.c.full_name.label("customer_name"),
            customers.c.tier.label("customer_tier"),
            request.c.id.label("request_id"),
            request.c.status.label("request_status"),
            request.c.requester_oid.label("request_requester_oid"),
            request.c.requester_name.label("request_requester_name"),
            request.c.approver_name.label("request_approver_name"),
            request.c.args.label("request_args"),
            refund.c.id.label("refund_id"),
            refund.c.status.label("refund_status"),
            refund.c.amount_minor.label("refund_amount_minor"),
            refund.c.currency.label("refund_currency"),
            refund.c.reason_code.label("refund_reason_code"),
            refund.c.requested_by_name.label("refund_requested_by_name"),
            refund.c.approved_by_name.label("refund_approved_by_name"),
            refund.c.failure_reason.label("refund_failure_reason"),
        )
        .join(customers, customers.c.id == tickets.c.customer_id)
        .outerjoin(request, true())
        .outerjoin(refund, true())
    )


def _request(row: RowMapping) -> RefundRequestRecord | None:
    if row["request_id"] is None:
        return None
    try:
        arguments = RefundArguments.model_validate(row["request_args"])
    except ValidationError:
        _LOG.warning("refund_request_unreadable", pending_action_id=str(row["request_id"]))
        return None
    return RefundRequestRecord(
        status=row["request_status"],
        requester_oid=row["request_requester_oid"],
        requester_name=row["request_requester_name"],
        approver_name=row["request_approver_name"],
        amount_minor=arguments.amount_minor,
        reason_code=arguments.reason_code,
    )


def _recommendation(row: RowMapping) -> RecommendationRecord | None:
    if row["recommendation"] is None:
        return None
    try:
        return RecommendationRecord.model_validate(row["recommendation"])
    except ValidationError:
        _LOG.warning("recommendation_unreadable", ticket_id=row["id"])
        return None


def _refund(row: RowMapping) -> RefundRecord | None:
    if row["refund_id"] is None:
        return None
    return RefundRecord(
        id=row["refund_id"],
        status=row["refund_status"],
        amount_minor=row["refund_amount_minor"],
        currency=row["refund_currency"],
        reason_code=row["refund_reason_code"],
        requested_by_name=row["refund_requested_by_name"],
        approved_by_name=row["refund_approved_by_name"],
        failure_reason=row["refund_failure_reason"],
    )


def _record(row: RowMapping) -> TicketRecord:
    return TicketRecord(
        id=row["id"],
        order_id=row["order_id"],
        customer_id=row["customer_id"],
        customer_name=row["customer_name"],
        customer_tier=row["customer_tier"],
        region=row["region"],
        status=row["status"],
        priority=row["priority"],
        queue=row["queue"],
        summary=row["summary"],
        escalated_by_name=row["escalated_by_name"],
        escalated_at=row["escalated_at"],
        recommendation=_recommendation(row),
        version=row["version"],
        updated_at=row["updated_at"],
        refund_request=_request(row),
        latest_refund=_refund(row),
    )


def _before(position: TicketPosition) -> ColumnElement[bool]:
    return tuple_(tickets.c.updated_at, tickets.c.id) < tuple_(
        literal(position.updated_at, DateTime(timezone=True)), literal(position.id, BigInteger)
    )


class PostgresTickets:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(
        self, scope: Scope, ticket_id: int, *, notes: int, now: datetime
    ) -> TicketWithNotes | None:
        async def find(connection: AsyncConnection) -> TicketWithNotes | None:
            row = (
                (await connection.execute(_tickets(scope, now).where(tickets.c.id == ticket_id)))
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            return TicketWithNotes(
                ticket=_record(row), notes=await _newest_notes(connection, ticket_id, notes)
            )

        return await self._database.read(scope, find)

    async def page(
        self, scope: Scope, request: TicketPageRequest, *, now: datetime
    ) -> TicketPageRecord:
        async def find(connection: AsyncConnection) -> TicketPageRecord:
            return TicketPageRecord(
                tickets=await _page(connection, scope, request, now),
                counts=await _counts(connection, request, now),
            )

        return await self._database.read(scope, find)


async def _newest_notes(
    connection: AsyncConnection, ticket_id: int, limit: int
) -> tuple[NoteRecord, ...]:
    if not limit:
        return ()
    rows = await connection.execute(
        select(
            ticket_notes.c.author_kind,
            ticket_notes.c.author_name,
            ticket_notes.c.body,
            ticket_notes.c.created_at,
        )
        .where(ticket_notes.c.ticket_id == ticket_id)
        .order_by(ticket_notes.c.created_at.desc(), ticket_notes.c.id.desc())
        .limit(limit)
    )
    return tuple(NoteRecord.model_validate(dict(row)) for row in rows.mappings())


async def _page(
    connection: AsyncConnection, scope: Scope, request: TicketPageRequest, now: datetime
) -> tuple[TicketRecord, ...]:
    if request.matching is None:
        return ()
    statement = _tickets(scope, now).where(*_conditions(request.matching, now))
    if request.after is not None:
        statement = statement.where(_before(request.after))
    rows = await connection.execute(statement.order_by(*_NEWEST_UPDATE_FIRST).limit(request.limit))
    return tuple(_record(row) for row in rows.mappings())


async def _counts(
    connection: AsyncConnection, request: TicketPageRequest, now: datetime
) -> dict[str, int]:
    if not request.counted:
        return {}
    capped = [
        select(func.count())
        .select_from(
            select(tickets.c.id)
            .where(*_conditions(wanted, now))
            .limit(request.count_cap)
            .subquery()
        )
        .scalar_subquery()
        for wanted in request.counted.values()
    ]
    row = (await connection.execute(select(*capped))).one()
    return dict(zip(request.counted, row, strict=True))

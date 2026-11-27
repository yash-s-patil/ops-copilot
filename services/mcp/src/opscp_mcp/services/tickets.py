import base64
import binascii
from datetime import UTC, datetime
from typing import Literal

from pydantic import Field

from opscp_common.errors import NotFoundError, ValidationFailedError
from opscp_common.limits import Limits
from opscp_common.vocabulary import (
    REFUND_FAILED,
    REQUEST_PROPOSED,
    AuthorKind,
    Priority,
    Queue,
    RefundReason,
    TicketStatus,
    Tier,
)
from opscp_mcp.authz.identity import Identity
from opscp_mcp.authz.policy import Permission
from opscp_mcp.ports.meridian import (
    NoteRecord,
    TicketFilter,
    TicketPageRequest,
    TicketPosition,
    TicketRecord,
    Tickets,
)
from opscp_mcp.services.caps import CappedText, Result, cap_text
from opscp_mcp.services.inputs import require_id
from opscp_mcp.services.orders import Clock
from opscp_mcp.services.queues import VIEWS, View, filter_for
from opscp_mcp.services.scope import scope_of

type RefundState = Literal[
    "waiting_for_supervisor",
    "approved_by_supervisor",
    "approved",
    "sent_to_payments",
    "paid",
    "failed",
]

_REQUEST_CURRENCY = "INR"


class TicketSummary(Result):
    ticket_id: int
    order_id: int
    status: TicketStatus
    priority: Priority
    queue: Queue
    summary: str
    summary_truncated: bool
    customer_name: str
    customer_tier: Tier
    escalated_by_name: str | None
    has_recommendation: bool
    refund_state: RefundState | None
    needs_my_approval: bool
    updated_at: datetime


class TicketPage(Result):
    items: tuple[TicketSummary, ...]
    next_cursor: str | None
    counts: dict[View, int]


class RecommendationView(Result):
    amount_minor: int
    reason_code: RefundReason
    reason_text: str
    reason_text_truncated: bool


class RefundView(Result):
    state: RefundState
    amount_minor: int
    currency: str
    reason_code: RefundReason
    requested_by_name: str
    approved_by_name: str | None
    failure_reason: str | None
    refund_id: int | None


class NoteView(Result):
    author_kind: AuthorKind
    author_name: str
    body: str
    truncated: bool
    created_at: datetime


class TicketView(Result):
    ticket_id: int
    order_id: int
    customer_id: int
    status: TicketStatus
    priority: Priority
    queue: Queue
    summary: str
    summary_truncated: bool
    escalated_by_name: str | None
    escalated_at: datetime | None
    recommendation: RecommendationView | None
    version: int
    updated_at: datetime
    needs_my_approval: bool
    refund: RefundView | None
    notes: tuple[NoteView, ...]


class TicketQuery(Result):
    view: View | None = None
    order_id: int | None = None
    customer_id: int | None = None
    status: TicketStatus | None = None
    cursor: str | None = Field(default=None, repr=False)
    page_size: int | None = None


class TicketService:
    def __init__(self, tickets: Tickets, limits: Limits, clock: Clock) -> None:
        self._tickets = tickets
        self._limits = limits
        self._clock = clock

    async def get(
        self, identity: Identity, ticket_id: int, *, notes: int | None = None
    ) -> TicketView:
        identity.require(Permission.TICKETS_READ)
        shown = self._limits.notes.default if notes is None else notes
        if not 0 <= shown <= self._limits.notes.max:
            raise ValidationFailedError(
                f"notes must be between 0 and {self._limits.notes.max}.", max=self._limits.notes.max
            )
        found = await self._tickets.get(
            scope_of(identity), require_id(ticket_id, "ticket_id"), notes=shown, now=self._clock()
        )
        if found is None:
            raise NotFoundError("No ticket with that number is visible to you.")
        ticket = found.ticket
        summary = self._summary(ticket)
        return TicketView(
            ticket_id=ticket.id,
            order_id=ticket.order_id,
            customer_id=ticket.customer_id,
            status=ticket.status,
            priority=ticket.priority,
            queue=ticket.queue,
            summary=summary.text,
            summary_truncated=summary.truncated,
            escalated_by_name=self._optional_name(ticket.escalated_by_name),
            escalated_at=ticket.escalated_at,
            recommendation=self._recommendation(ticket),
            version=ticket.version,
            updated_at=ticket.updated_at,
            needs_my_approval=needs_approval_from(identity, ticket),
            refund=self._refund(ticket),
            notes=tuple(self._note(note) for note in found.notes),
        )

    async def list(self, identity: Identity, query: TicketQuery) -> TicketPage:
        identity.require(Permission.TICKETS_READ)
        size = self._page_size(query.page_size)
        views = {name: filter_for(name, identity) for name in VIEWS}
        found = await self._tickets.page(
            scope_of(identity),
            TicketPageRequest(
                matching=_filter(query, identity),
                after=self._position(query.cursor),
                limit=size + 1,
                counted={name: wanted for name, wanted in views.items() if wanted is not None},
                count_cap=self._limits.tickets.count_max,
            ),
            now=self._clock(),
        )
        records = found.tickets
        return TicketPage(
            items=tuple(self._item(identity, record) for record in records[:size]),
            next_cursor=_cursor(records[size - 1]) if len(records) > size else None,
            counts={name: found.counts.get(name, 0) for name in VIEWS},
        )

    def _position(self, cursor: str | None) -> TicketPosition | None:
        if cursor is None:
            return None
        try:
            if len(cursor) > self._limits.cursor.chars:
                raise ValueError(cursor)
            position = TicketPosition.model_validate_json(base64.urlsafe_b64decode(cursor.encode()))
            return TicketPosition(
                updated_at=position.updated_at.astimezone(UTC), id=require_id(position.id, "cursor")
            )
        except (binascii.Error, ValueError, OverflowError) as error:
            raise ValidationFailedError("cursor is not one this server issued.") from error

    def _page_size(self, requested: int | None) -> int:
        limits = self._limits.tickets
        size = limits.page_size_default if requested is None else requested
        if not 0 < size <= limits.page_size_max:
            raise ValidationFailedError(
                f"page_size must be between 1 and {limits.page_size_max}.", max=limits.page_size_max
            )
        return size

    def _item(self, identity: Identity, ticket: TicketRecord) -> TicketSummary:
        summary = self._summary(ticket)
        refund = self._refund(ticket)
        return TicketSummary(
            ticket_id=ticket.id,
            order_id=ticket.order_id,
            status=ticket.status,
            priority=ticket.priority,
            queue=ticket.queue,
            summary=summary.text,
            summary_truncated=summary.truncated,
            customer_name=self._name(ticket.customer_name),
            customer_tier=ticket.customer_tier,
            escalated_by_name=self._optional_name(ticket.escalated_by_name),
            has_recommendation=ticket.recommendation is not None,
            refund_state=None if refund is None else refund.state,
            needs_my_approval=needs_approval_from(identity, ticket),
            updated_at=ticket.updated_at,
        )

    def _summary(self, ticket: TicketRecord) -> CappedText:
        limits = self._limits.ticket
        return cap_text(ticket.summary, limits.summary_chars, limits.summary_bytes)

    def _recommendation(self, ticket: TicketRecord) -> RecommendationView | None:
        advice = ticket.recommendation
        if advice is None:
            return None
        reason = self._reason(advice.reason_text)
        return RecommendationView(
            amount_minor=advice.amount_minor,
            reason_code=advice.reason_code,
            reason_text=reason.text,
            reason_text_truncated=reason.truncated,
        )

    def _name(self, name: str) -> str:
        return cap_text(name, self._limits.name.chars, self._limits.name.bytes).text

    def _optional_name(self, name: str | None) -> str | None:
        return None if name is None else self._name(name)

    def _reason(self, text: str) -> CappedText:
        return cap_text(text, self._limits.refund.reason_chars, self._limits.refund.reason_bytes)

    def _refund(self, ticket: TicketRecord) -> RefundView | None:
        refund = refund_of(ticket)
        if refund is None:
            return None
        # A refund sent round again keeps its old reason until payments answers.
        failure = refund.failure_reason if refund.state == REFUND_FAILED else None
        return refund.model_copy(
            update={
                "requested_by_name": self._name(refund.requested_by_name),
                "approved_by_name": self._optional_name(refund.approved_by_name),
                "failure_reason": None if failure is None else self._reason(failure).text,
            }
        )

    def _note(self, note: NoteRecord) -> NoteView:
        limits = self._limits.note
        body = cap_text(note.body, limits.chars, limits.bytes)
        return NoteView(
            author_kind=note.author_kind,
            author_name=self._name(note.author_name),
            body=body.text,
            truncated=body.truncated,
            created_at=note.created_at,
        )


def refund_of(ticket: TicketRecord) -> RefundView | None:
    """One refund per ticket: a request still waiting to be carried out, else the latest refund."""
    request, refund = ticket.refund_request, ticket.latest_refund
    if request is not None:
        waiting = request.status == REQUEST_PROPOSED
        return RefundView(
            state="waiting_for_supervisor" if waiting else "approved_by_supervisor",
            amount_minor=request.amount_minor,
            currency=_REQUEST_CURRENCY,
            reason_code=request.reason_code,
            requested_by_name=request.requester_name,
            approved_by_name=request.approver_name,
            failure_reason=None,
            refund_id=None,
        )
    if refund is not None:
        return RefundView(
            state=refund.status,
            amount_minor=refund.amount_minor,
            currency=refund.currency,
            reason_code=refund.reason_code,
            requested_by_name=refund.requested_by_name,
            approved_by_name=refund.approved_by_name,
            failure_reason=refund.failure_reason,
            refund_id=refund.id,
        )
    return None


def needs_approval_from(identity: Identity, ticket: TicketRecord) -> bool:
    request = ticket.refund_request
    return (
        request is not None
        and request.status == REQUEST_PROPOSED
        and request.requester_oid != identity.oid
        and identity.can(Permission.REFUNDS_APPROVE)
    )


def _filter(query: TicketQuery, identity: Identity) -> TicketFilter | None:
    if query.view is not None:
        base = filter_for(query.view, identity)
    elif query.order_id is None and query.customer_id is None:
        raise ValidationFailedError("Give a view, an order_id or a customer_id.")
    else:
        base = TicketFilter()
    if base is None:
        return None
    statuses = base.statuses
    if query.status is not None:
        statuses = frozenset({query.status}) if statuses is None else statuses & {query.status}
    return base.model_copy(
        update={
            "statuses": statuses,
            "order_id": _optional_id(query.order_id, "order_id"),
            "customer_id": _optional_id(query.customer_id, "customer_id"),
        }
    )


def _optional_id(value: int | None, name: str) -> int | None:
    return None if value is None else require_id(value, name)


def _cursor(last: TicketRecord) -> str:
    position = TicketPosition(updated_at=last.updated_at, id=last.id)
    return base64.urlsafe_b64encode(position.model_dump_json().encode()).decode()

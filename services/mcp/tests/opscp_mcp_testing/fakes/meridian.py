"""In-memory stand-ins for the meridian ports, scoped by region as row-level security is."""

from collections.abc import Callable
from datetime import datetime

from opscp_common.vocabulary import (
    ALL_REGIONS,
    CARRIER_SCAN,
    ISSUE_REFUND,
    OPEN_TICKET_STATUSES,
    REFUND_FAILED,
    REFUND_SENT,
    REQUEST_APPROVED,
    REQUEST_PROPOSED,
    SHIPPED,
    SUPERVISOR_APPROVAL,
    EventType,
    Region,
)
from opscp_mcp.ports.meridian import (
    CustomerRecord,
    EventRecord,
    NoteRecord,
    OrderRecord,
    RecommendationRecord,
    RefundArguments,
    RefundRecord,
    RefundRequestRecord,
    Scope,
    TicketFilter,
    TicketPageRecord,
    TicketPageRequest,
    TicketRecord,
    TicketWithNotes,
)
from opscp_mcp_testing.world import (
    CustomerRow,
    EventRow,
    PendingActionRow,
    RefundRow,
    TicketRow,
    World,
)


def _visible(scope: Scope, region: Region) -> bool:
    return scope.region in (ALL_REGIONS, region)


class FakeCustomers:
    def __init__(self, world: World) -> None:
        self._world = world

    async def by_id(self, scope: Scope, customer_id: int) -> CustomerRecord | None:
        return self._first(scope, lambda row: row.id == customer_id)

    async def by_email(self, scope: Scope, email: str) -> CustomerRecord | None:
        return self._first(scope, lambda row: row.email.lower() == email.lower())

    def _first(self, scope: Scope, matches: Callable[[CustomerRow], bool]) -> CustomerRecord | None:
        for row in self._world.customers:
            if matches(row) and _visible(scope, row.region):
                return CustomerRecord(
                    id=row.id,
                    full_name=row.full_name,
                    email=row.email,
                    phone=row.phone,
                    tier=row.tier,
                    region=row.region,
                    created_at=row.created_at,
                    open_ticket_count=sum(
                        ticket.customer_id == row.id and ticket.status in OPEN_TICKET_STATUSES
                        for ticket in self._world.tickets
                    ),
                )
        return None


class FakeOrders:
    def __init__(self, world: World) -> None:
        self._world = world

    async def get(self, scope: Scope, order_id: int, *, recent_events: int) -> OrderRecord | None:
        row = next((order for order in self._world.orders if order.id == order_id), None)
        if row is None or not _visible(scope, row.region):
            return None
        events = sorted(
            (event for event in self._world.events if event.order_id == order_id),
            key=lambda event: event.occurred_at,
        )
        counts: dict[EventType, int] = {}
        for event in events:
            counts[event.event_type] = counts.get(event.event_type, 0) + 1
        return OrderRecord(
            id=row.id,
            customer_id=row.customer_id,
            status=row.status,
            amount_minor=row.amount_minor,
            currency=row.currency,
            region=row.region,
            carrier=row.carrier,
            tracking_ref=row.tracking_ref,
            placed_at=row.placed_at,
            status_changed_at=row.status_changed_at,
            refunded_minor=sum(
                refund.amount_minor
                for refund in self._world.refunds
                if refund.order_id == order_id and refund.status != REFUND_FAILED
            ),
            last_new_scan_at=_last_new_scan_at(events) if row.status == SHIPPED else None,
            recent_events=tuple(
                EventRecord(
                    type=event.event_type, occurred_at=event.occurred_at, payload=event.payload
                )
                for event in reversed(events[len(events) - min(recent_events, len(events)) :])
            ),
            event_counts=counts if recent_events else {},
        )


def _last_new_scan_at(events: list[EventRow]) -> datetime | None:
    scans = [event for event in events if event.event_type == CARRIER_SCAN]
    new = [
        scan.occurred_at
        for index, scan in enumerate(scans)
        if index == 0 or scan.payload != scans[index - 1].payload
    ]
    return max(new, default=None)


class FakeTickets:
    def __init__(self, world: World) -> None:
        self._world = world

    async def get(
        self, scope: Scope, ticket_id: int, *, notes: int, now: datetime
    ) -> TicketWithNotes | None:
        row = next((ticket for ticket in self._world.tickets if ticket.id == ticket_id), None)
        if row is None or not _visible(scope, row.region):
            return None
        newest = sorted(
            (note for note in self._world.notes if note.ticket_id == ticket_id),
            key=lambda note: note.created_at,
            reverse=True,
        )
        return TicketWithNotes(
            ticket=self._record(scope, row, now),
            notes=tuple(
                NoteRecord(
                    author_kind=note.author_kind,
                    author_name=note.author_name,
                    body=note.body,
                    created_at=note.created_at,
                )
                for note in newest[:notes]
            ),
        )

    async def page(
        self, scope: Scope, request: TicketPageRequest, *, now: datetime
    ) -> TicketPageRecord:
        rows: list[TicketRow] = []
        if request.matching is not None:
            rows = sorted(
                self._matching(scope, request.matching, now),
                key=lambda row: (row.updated_at, row.id),
                reverse=True,
            )
        after = request.after
        if after is not None:
            rows = [row for row in rows if (row.updated_at, row.id) < (after.updated_at, after.id)]
        return TicketPageRecord(
            tickets=tuple(self._record(scope, row, now) for row in rows[: request.limit]),
            counts={
                name: min(len(self._matching(scope, wanted, now)), request.count_cap)
                for name, wanted in request.counted.items()
            },
        )

    def _matching(self, scope: Scope, matching: TicketFilter, now: datetime) -> list[TicketRow]:
        return [
            row
            for row in self._world.tickets
            if _visible(scope, row.region) and self._matches(row, matching, now)
        ]

    def _matches(self, row: TicketRow, matching: TicketFilter, now: datetime) -> bool:
        proposed = [
            action
            for action in self._live_requests(row.id, now)
            if action.status == REQUEST_PROPOSED
        ]
        sent = any(
            refund.ticket_id == row.id and refund.status == REFUND_SENT
            for refund in self._world.refunds
        )
        somebody_else = matching.awaiting_approval_not_requested_by
        return all(
            (
                matching.statuses is None or row.status in matching.statuses,
                matching.queue in (None, row.queue),
                matching.order_id in (None, row.order_id),
                matching.customer_id in (None, row.customer_id),
                matching.escalated_by in (None, row.escalated_by_oid),
                matching.refund_in_flight in (None, bool(proposed) or sent),
                somebody_else is None
                or any(action.requester_oid != somebody_else for action in proposed),
            )
        )

    def _live_requests(self, ticket_id: int, now: datetime) -> list[PendingActionRow]:
        return [
            action
            for action in self._world.pending_actions
            if action.ticket_id == ticket_id
            and action.tool == ISSUE_REFUND
            and action.approval_kind == SUPERVISOR_APPROVAL
            and action.status in (REQUEST_PROPOSED, REQUEST_APPROVED)
            and action.expires_at > now
        ]

    def _record(self, scope: Scope, row: TicketRow, now: datetime) -> TicketRecord:
        customer = next(
            customer for customer in self._world.customers if customer.id == row.customer_id
        )
        request = max(
            self._live_requests(row.id, now),
            key=lambda action: (
                action.status == REQUEST_PROPOSED and action.requester_oid != scope.user_oid,
                action.created_at,
                action.id,
            ),
            default=None,
        )
        refund = max(
            (refund for refund in self._world.refunds if refund.ticket_id == row.id),
            key=lambda refund: (refund.created_at, refund.id),
            default=None,
        )
        return TicketRecord(
            id=row.id,
            order_id=row.order_id,
            customer_id=row.customer_id,
            customer_name=customer.full_name,
            customer_tier=customer.tier,
            region=row.region,
            status=row.status,
            priority=row.priority,
            queue=row.queue,
            summary=row.summary,
            escalated_by_name=row.escalated_by_name,
            escalated_at=row.escalated_at,
            recommendation=None
            if row.recommendation is None
            else RecommendationRecord.model_validate(row.recommendation),
            version=row.version,
            updated_at=row.updated_at,
            refund_request=None if request is None else _request(request),
            latest_refund=None if refund is None else _refund(refund),
        )


def _request(action: PendingActionRow) -> RefundRequestRecord:
    arguments = RefundArguments.model_validate(action.args)
    return RefundRequestRecord.model_validate(
        {
            "status": action.status,
            "requester_oid": action.requester_oid,
            "requester_name": action.requester_name,
            "approver_name": action.approver_name,
            "amount_minor": arguments.amount_minor,
            "reason_code": arguments.reason_code,
        }
    )


def _refund(refund: RefundRow) -> RefundRecord:
    return RefundRecord(
        id=refund.id,
        status=refund.status,
        amount_minor=refund.amount_minor,
        currency="INR",
        reason_code=refund.reason_code,
        requested_by_name=refund.requested_by_name,
        approved_by_name=refund.approved_by_name,
        failure_reason=refund.failure_reason,
    )

"""What the services need from the meridian database, whatever stores it."""

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

from opscp_common.vocabulary import (
    AuthorKind,
    CallerRegion,
    EventType,
    OrderStatus,
    Priority,
    Queue,
    RefundReason,
    RefundStatus,
    Region,
    RequestStatus,
    TicketStatus,
    Tier,
)


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Scope(_Record):
    """Whose rows a read may see. Every read is made inside exactly one scope."""

    user_oid: UUID
    region: CallerRegion


class CustomerRecord(_Record):
    id: int
    full_name: str
    email: str
    phone: str | None
    tier: Tier
    region: Region
    created_at: datetime
    open_ticket_count: int


class EventRecord(_Record):
    type: EventType
    occurred_at: datetime
    payload: dict[str, JsonValue]


class OrderRecord(_Record):
    id: int
    customer_id: int
    status: OrderStatus
    amount_minor: int
    currency: str
    region: Region
    carrier: str | None
    tracking_ref: str | None
    placed_at: datetime
    status_changed_at: datetime
    refunded_minor: int
    last_new_scan_at: datetime | None
    recent_events: tuple[EventRecord, ...]
    event_counts: dict[EventType, int]


class _StoredJson(BaseModel):
    """Read from a JSON column, which may hold more than the fields used here."""

    model_config = ConfigDict(extra="ignore", frozen=True)


class RecommendationRecord(_StoredJson):
    amount_minor: int
    reason_code: RefundReason
    reason_text: str


class RefundArguments(_StoredJson):
    amount_minor: int = Field(gt=0)
    reason_code: RefundReason


class RefundRequestRecord(_Record):
    """A refund above the self-approval limit that has been asked for and not yet carried out."""

    status: RequestStatus
    requester_oid: UUID
    requester_name: str
    approver_name: str | None
    amount_minor: int
    reason_code: RefundReason


class RefundRecord(_Record):
    id: int
    status: RefundStatus
    amount_minor: int
    currency: str
    reason_code: RefundReason
    requested_by_name: str
    approved_by_name: str
    failure_reason: str | None


class TicketRecord(_Record):
    id: int
    order_id: int
    customer_id: int
    customer_name: str
    customer_tier: Tier
    region: Region
    status: TicketStatus
    priority: Priority
    queue: Queue
    summary: str
    escalated_by_name: str | None
    escalated_at: datetime | None
    recommendation: RecommendationRecord | None
    version: int
    updated_at: datetime
    refund_request: RefundRequestRecord | None
    latest_refund: RefundRecord | None


class NoteRecord(_Record):
    author_kind: AuthorKind
    author_name: str
    body: str
    created_at: datetime


class TicketWithNotes(_Record):
    ticket: TicketRecord
    notes: tuple[NoteRecord, ...]


class TicketFilter(_Record):
    """Every condition that is set must hold."""

    statuses: frozenset[TicketStatus] | None = None
    queue: Queue | None = None
    order_id: int | None = None
    customer_id: int | None = None
    escalated_by: UUID | None = None
    refund_in_flight: bool | None = None
    """A refund is waiting for a supervisor or has been sent to payments; False for neither."""
    awaiting_approval_not_requested_by: UUID | None = None
    """A refund is waiting for a supervisor and was asked for by somebody else."""


class TicketPosition(_Record):
    """Where a page ended, in the fixed order of ticket lists: newest update first."""

    updated_at: AwareDatetime
    id: int


class TicketPageRequest(_Record):
    matching: TicketFilter | None
    """None lists nothing; the counts are still made."""
    after: TicketPosition | None
    limit: int
    counted: Mapping[str, TicketFilter]
    count_cap: int


class TicketPageRecord(_Record):
    tickets: tuple[TicketRecord, ...]
    counts: dict[str, int]


class Customers(Protocol):
    async def by_id(self, scope: Scope, customer_id: int) -> CustomerRecord | None: ...

    async def by_email(self, scope: Scope, email: str) -> CustomerRecord | None:
        """Matched without regard to case."""
        ...


class Orders(Protocol):
    async def get(self, scope: Scope, order_id: int, *, recent_events: int) -> OrderRecord | None:
        """`recent_events` are newest first. `event_counts` covers every event of the order.

        Asking for no recent events leaves both empty. `last_new_scan_at` is set only for a
        shipped order: the latest carrier scan that differs from the scan before it, so a
        scanner repeating itself does not count as movement. `refunded_minor` leaves out
        refunds whose payment failed.
        """
        ...


class Tickets(Protocol):
    """`now` decides whether a refund request is still live: proposed or approved, and not
    past its expiry. Other requests are not reported.

    When a ticket has several live requests, the one reported is a proposed request made by
    somebody other than the caller if there is one, and otherwise the newest.
    """

    async def get(
        self, scope: Scope, ticket_id: int, *, notes: int, now: datetime
    ) -> TicketWithNotes | None:
        """Notes are newest first."""
        ...

    async def page(
        self, scope: Scope, request: TicketPageRequest, *, now: datetime
    ) -> TicketPageRecord:
        """One page and, from the same snapshot, a count per named filter up to the cap."""
        ...

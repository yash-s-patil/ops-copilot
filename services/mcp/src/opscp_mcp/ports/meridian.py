"""What the services need from the meridian database, whatever stores it."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue

from opscp_common.vocabulary import CallerRegion, EventType, OrderStatus, Region, Tier


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

"""Rows of the meridian database, as plain values."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from opscp_common.hashing import JsonValue

type Region = Literal["west", "south"]
type Tier = Literal["standard", "premium"]
type OrderStatus = Literal["placed", "paid", "packed", "shipped", "delivered", "cancelled", "lost"]
type EventType = Literal[
    "payment_hold",
    "payment_released",
    "address_validation_failed",
    "packed",
    "carrier_pickup",
    "carrier_scan",
    "carrier_exception",
    "lost_in_transit_confirmed",
    "damage_reported",
    "delivered",
    "cancelled",
]
type TicketStatus = Literal["open", "pending_customer", "resolved", "closed"]
type Priority = Literal["P1", "P2", "P3", "P4"]
type Queue = Literal["L1", "L2"]
type AuthorKind = Literal["customer", "engineer"]
type RefundReason = Literal["goodwill_delay", "lost_in_transit", "damaged"]
type Json = dict[str, JsonValue]


class Engineer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    oid: UUID
    name: str = Field(min_length=1)


@dataclass(frozen=True, slots=True, kw_only=True)
class Customer:
    id: int
    full_name: str
    email: str
    phone: str
    tier: Tier
    region: Region
    created_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class Order:
    id: int
    customer_id: int
    status: OrderStatus
    amount_minor: int
    region: Region
    carrier: str | None
    tracking_ref: str | None
    placed_at: datetime
    status_changed_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderEvent:
    order_id: int
    event_type: EventType
    payload: Json
    occurred_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class Ticket:
    id: int
    order_id: int
    customer_id: int
    region: Region
    status: TicketStatus
    priority: Priority
    queue: Queue
    escalated_by_oid: UUID | None
    escalated_by_name: str | None
    escalated_at: datetime | None
    recommendation: Json | None
    summary: str
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class TicketNote:
    ticket_id: int
    author_kind: AuthorKind
    author_oid: UUID | None
    author_name: str
    body: str
    created_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class PendingAction:
    id: UUID
    tool: str
    args: Json
    args_hash: str
    requester_oid: UUID
    requester_name: str
    requester_region: Region
    approval_kind: Literal["self", "supervisor"]
    policy_snapshot: Json
    idempotency_key: str
    ticket_id: int
    expires_at: datetime
    created_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class Dataset:
    customers: tuple[Customer, ...]
    orders: tuple[Order, ...]
    events: tuple[OrderEvent, ...]
    tickets: tuple[Ticket, ...]
    notes: tuple[TicketNote, ...]
    pending_actions: tuple[PendingAction, ...]

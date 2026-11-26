"""The fixed words of the meridian database, as the check constraints spell them."""

from typing import Literal

type Region = Literal["west", "south"]
type CallerRegion = Region | Literal["*"]
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
type RefundStatus = Literal["approved", "sent_to_payments", "paid", "failed"]

OPEN_TICKET_STATUSES: tuple[TicketStatus, ...] = ("open", "pending_customer")
ALL_REGIONS: CallerRegion = "*"
CARRIER_SCAN: EventType = "carrier_scan"
SHIPPED: OrderStatus = "shipped"
REFUND_FAILED: RefundStatus = "failed"

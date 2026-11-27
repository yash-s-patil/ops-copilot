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
type AuthorKind = Literal["customer", "engineer", "system", "agent_on_behalf"]
type RefundStatus = Literal["approved", "sent_to_payments", "paid", "failed"]
type RequestStatus = Literal["proposed", "approved"]
type RefundReason = Literal["goodwill_delay", "lost_in_transit", "cancelled", "damaged"]

OPEN_TICKET_STATUSES: tuple[TicketStatus, ...] = ("open", "pending_customer")
ALL_REGIONS: CallerRegion = "*"
CARRIER_SCAN: EventType = "carrier_scan"
SHIPPED: OrderStatus = "shipped"
REFUND_FAILED: RefundStatus = "failed"
DONE_TICKET_STATUSES: tuple[TicketStatus, ...] = ("resolved", "closed")
REFUND_SENT: RefundStatus = "sent_to_payments"
ISSUE_REFUND = "issue_refund"
UPDATE_TICKET = "update_ticket"
GOODWILL_DELAY: RefundReason = "goodwill_delay"
REFUND_UNSETTLED: tuple[RefundStatus, ...] = ("approved", "sent_to_payments")
SUPERVISOR_APPROVAL = "supervisor"
REQUEST_PROPOSED: RequestStatus = "proposed"
REQUEST_APPROVED: RequestStatus = "approved"

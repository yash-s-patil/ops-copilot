"""Writes that have been asked for and wait for a person, and what a refund is judged on."""

from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from opscp_common.hashing import JsonValue
from opscp_common.vocabulary import CallerRegion, OrderStatus, Region, Tier
from opscp_mcp.ports.base import Record
from opscp_mcp.ports.controls import AuditEntry
from opscp_mcp.ports.meridian import Scope

type WriteTool = Literal["update_ticket", "issue_refund"]
type ApprovalKind = Literal["self", "supervisor"]
type ActionStatus = Literal["proposed", "approved", "executed", "rejected", "expired", "failed"]


class RefundCase(Record):
    """What is known about an order when a refund on it is asked for."""

    order_id: int
    region: Region
    tier: Tier
    status: OrderStatus
    status_changed_at: datetime
    amount_minor: int
    refunded_minor: int
    """Refunds already made, leaving out those whose payment failed."""
    last_new_scan_at: datetime | None
    carrier_exception_at: datetime | None
    loss_confirmed: bool
    damage_reported: bool
    goodwill_given: bool
    request_waiting: bool
    """Another refund request on the order has not been decided or carried out yet."""


class RefundCases(Protocol):
    async def for_order(self, scope: Scope, order_id: int, *, now: datetime) -> RefundCase | None:
        """None when the order does not exist or is outside the scope."""
        ...

    async def unfinished_on_ticket(self, scope: Scope, ticket_id: int, *, now: datetime) -> bool:
        """Whether a refund tied to the ticket is still on its way.

        That is a refund approved or sent to payments and not yet settled, or a refund
        request, of either kind of approval, that has not been decided or carried out.
        """
        ...


class NewAction(Record):
    tool: WriteTool
    args: dict[str, JsonValue]
    args_hash: str
    requester_oid: UUID
    requester_name: str
    requester_region: CallerRegion
    approval_kind: ApprovalKind
    policy_snapshot: dict[str, JsonValue]
    idempotency_key: str
    ticket_id: int | None
    order_id: int | None
    """The order a refund is asked for on; None for any other write."""
    expires_at: datetime


class ActionRecord(Record):
    id: UUID
    tool: WriteTool
    args: dict[str, JsonValue]
    args_hash: str
    requester_oid: UUID
    requester_name: str
    requester_region: CallerRegion
    approval_kind: ApprovalKind
    status: ActionStatus
    approver_oid: UUID | None
    approver_name: str | None
    decided_at: datetime | None
    decision_reason: str | None
    policy_snapshot: dict[str, JsonValue] | None
    ticket_id: int | None
    result: dict[str, JsonValue] | None
    expires_at: datetime
    created_at: datetime


class Decision(Record):
    """What a person decided about a proposed action, and until when the outcome stands."""

    status: Literal["approved", "rejected"]
    seen_hash: str
    """The fingerprint the decision was made on. The action is left alone if it differs."""
    approver_oid: UUID
    approver_name: str
    reason: str | None
    decided_at: datetime
    expires_at: datetime


class Decided(Record):
    action: ActionRecord
    audit_id: int


class ActionsWanted(Record):
    """The live actions a person asked for, or that wait for someone of their standing."""

    requester_oid: UUID
    awaiting_supervisor_in: CallerRegion | None
    """Set for a caller who may approve: the region whose requests they may decide."""
    ticket_id: int | None
    limit: int


class PendingActions(Protocol):
    async def find(
        self, scope: Scope, tool: WriteTool, idempotency_key: str
    ) -> ActionRecord | None:
        """The action the caller in scope made earlier with this key, if any."""
        ...

    async def propose(self, new: NewAction, *, now: datetime) -> ActionRecord | None:
        """Stores the action, or returns the one already stored under the same key.

        Returns None, storing nothing, when the action is a refund on an order that already
        has a refund request waiting. Requests on one order are taken one at a time, so two
        that arrive together cannot both be stored.
        """
        ...

    async def get(self, action_id: UUID) -> ActionRecord | None:
        """Any action by its id. Who may see it is for the caller of this to decide."""
        ...

    async def decide(
        self, action_id: UUID, decision: Decision, entry: AuditEntry
    ) -> Decided | None:
        """Records the decision, and its audit entry with it, or neither.

        The action must still be proposed, not past its expiry, and carry the fingerprint
        the decision was made on. Returns None, changing nothing, when it does not: two
        decisions cannot both be taken.
        """
        ...

    async def waiting(self, wanted: ActionsWanted, *, now: datetime) -> tuple[ActionRecord, ...]:
        """Proposed and approved actions that have not expired, newest first."""
        ...

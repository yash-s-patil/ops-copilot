"""Carrying out an approved action: one transaction, taken once.

The store claims the action, uses up the approval token, lets the service do the write
through a ledger, and records the outcome and the audit entry. Either all of that is kept or
none of it. A write the rules refuse at that moment marks the action failed, and that much
is kept.
"""

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Protocol
from uuid import UUID

from opscp_common.errors import ErrorCode, OpsError
from opscp_common.hashing import JsonValue
from opscp_common.vocabulary import Queue, RefundReason, Region, TicketStatus
from opscp_mcp.ports.actions import ApprovalKind, RefundCase, WriteTool
from opscp_mcp.ports.base import Record
from opscp_mcp.ports.controls import AuditEntry
from opscp_mcp.ports.meridian import Scope


class Claim(Record):
    """Which action is to be carried out, with which token, by whom, on what fingerprint."""

    action_id: UUID
    token_id: UUID
    approver_oid: UUID
    args_hash: str
    scope: Scope
    order_id: int | None
    """Set for a refund: writes on one order are made one at a time."""
    now: datetime


class ApprovedAction(Record):
    """The stored action as it was claimed: what the write is made from."""

    id: UUID
    tool: WriteTool
    args: dict[str, JsonValue]
    approval_kind: ApprovalKind
    requester_oid: UUID
    requester_name: str
    approver_oid: UUID
    approver_name: str
    policy_snapshot: dict[str, JsonValue] | None


class TicketState(Record):
    id: int
    order_id: int
    region: Region
    status: TicketStatus
    queue: Queue
    version: int


class NewRefund(Record):
    order_id: int
    ticket_id: int | None
    region: Region
    amount_minor: int
    reason_code: RefundReason
    reason_text: str


class Escalation(Record):
    by_oid: UUID
    by_name: str
    recommendation: dict[str, JsonValue] | None


class TicketChange(Record):
    ticket_id: int
    from_version: int
    status: TicketStatus | None
    escalation: Escalation | None


class NewNote(Record):
    ticket_id: int
    author_oid: UUID
    author_name: str
    body: str


class Ledger(Protocol):
    """What the write may read and change inside the transaction, in the caller's scope."""

    async def refund_case(self, order_id: int) -> RefundCase | None: ...

    async def ticket(self, ticket_id: int) -> TicketState | None:
        """The ticket, held so that nobody else changes it before this write ends."""
        ...

    async def refund_unfinished_on(self, ticket_id: int) -> bool: ...

    async def add_refund(self, refund: NewRefund) -> int:
        """Writes the refund and the row that tells payments to pay it. Returns its id."""
        ...

    async def change_ticket(self, change: TicketChange) -> TicketState | None:
        """None when the ticket is no longer at the version the change was made for."""
        ...

    async def add_note(self, note: NewNote) -> None: ...


type Work = Callable[[ApprovedAction, Ledger], Awaitable[dict[str, JsonValue]]]
type EntryFor = Callable[[OpsError | None], AuditEntry]


class Executed(Record):
    result: dict[str, JsonValue]
    audit_id: int


def is_refusal_by_the_rules(error: OpsError) -> bool:
    """A refusal that waiting will not change. An outage or a defect is not one."""
    return not error.retryable and error.code is not ErrorCode.INTERNAL


class RefusedAtExecutionError(Exception):
    """The rules refused the write. The action is now failed, and that is recorded."""

    def __init__(self, refusal: OpsError, audit_id: int) -> None:
        super().__init__(type(refusal).__name__)
        self.refusal = refusal
        self.audit_id = audit_id


class Executions(Protocol):
    async def run(self, claim: Claim, work: Work, entry: EntryFor) -> Executed | None:
        """Carries the action out, or returns None when the claim does not hold.

        The claim holds only if the action is approved, not expired, approved by the named
        approver, asked for by the caller in scope, carries the fingerprint given, and the
        token has not been used. Raises RefusedAtExecutionError when `work` is refused by
        the rules: nothing it wrote is kept, and the action is marked failed. Any other
        failure leaves the action approved and the token unused, so the call can be repeated.
        """
        ...

"""The first half of every write: check it, record it as waiting, and change nothing.

A write tool called without an approval creates a pending action and answers "approval
required". No business row changes here. A person approves the action, and only then is the
write carried out, with exactly the arguments recorded now: a call that brings an approval
is validated the same way and handed on to be carried out.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol
from uuid import UUID

from opscp_common.errors import (
    IdempotencyConflictError,
    NotFoundError,
    PolicyViolationError,
    ValidationFailedError,
)
from opscp_common.hashing import JsonValue, args_hash
from opscp_common.limits import Limits
from opscp_common.vocabulary import ISSUE_REFUND, UPDATE_TICKET, RefundReason, TicketStatus
from opscp_mcp.authz.identity import Identity
from opscp_mcp.authz.policy import Permission
from opscp_mcp.ports.actions import (
    ActionRecord,
    ActionStatus,
    ApprovalKind,
    NewAction,
    PendingActions,
    RefundCase,
    RefundCases,
    WriteTool,
)
from opscp_mcp.ports.meridian import TicketRecord, Tickets
from opscp_mcp.services import refund_policy
from opscp_mcp.services.caps import Result
from opscp_mcp.services.inputs import require_id
from opscp_mcp.services.orders import Clock
from opscp_mcp.services.scope import scope_of

type ProposalStatus = Literal["approval_required", "executed", "rejected", "expired", "failed"]
type EscalateTo = Literal["L2"]

KEY_MAX_CHARS = 64
# The largest whole number every JSON reader agrees on, so that arguments hash one way.
LARGEST_NUMBER = 2**53 - 1
STATUS_MOVES: dict[TicketStatus, tuple[TicketStatus, ...]] = {
    "open": ("pending_customer", "resolved"),
    "pending_customer": ("open", "resolved"),
    "resolved": ("closed",),
    "closed": (),
}
# Joiners that Devanagari, Malayalam and other scripts need inside a word.
_JOINERS = "\u200c\u200d"
_ANSWERS: dict[ActionStatus, ProposalStatus] = {
    "proposed": "approval_required",
    "approved": "approval_required",
    "executed": "executed",
    "rejected": "rejected",
    "expired": "expired",
    "failed": "failed",
}
_KEY_PUNCTUATION = "-_"
_MINOR_UNITS = 100


class Recommendation(Result):
    amount_minor: int
    reason_code: RefundReason
    reason_text: str


class RefundRequest(Result):
    order_id: int
    amount_minor: int
    reason_code: RefundReason
    reason_text: str
    ticket_id: int | None = None


class TicketUpdate(Result):
    ticket_id: int
    status: TicketStatus | None = None
    note: str | None = None
    escalate_to: EscalateTo | None = None
    recommendation: Recommendation | None = None


class Proposal(Result):
    status: ProposalStatus
    action_id: UUID
    args_hash: str
    approval_kind: ApprovalKind
    summary: str
    expires_at: datetime
    result: dict[str, int | str] | None = None
    """What was written, once the action has been carried out."""


def written(result: dict[str, JsonValue] | None) -> dict[str, int | str] | None:
    if result is None:
        return None
    return {name: value for name, value in result.items() if isinstance(value, int | str)}


def _rupees(amount_minor: int) -> str:
    return f"INR {amount_minor // _MINOR_UNITS:,}.{amount_minor % _MINOR_UNITS:02d}"


def summary_of(tool: WriteTool, args: dict[str, JsonValue]) -> str:
    """One line naming the change, built from the recorded arguments and none of their text."""
    if tool == ISSUE_REFUND:
        amount = args["amount_minor"]
        shown = _rupees(amount) if isinstance(amount, int) else "an amount"
        return f"Refund {shown} on order {args['order_id']} ({args['reason_code']})"
    changes = [
        f"set status to {args['status']}" if "status" in args else None,
        "add a note" if "note" in args else None,
        "escalate to L2" if "escalate_to" in args else None,
        "recommend a refund" if "recommendation" in args else None,
    ]
    return f"Ticket {args['ticket_id']}: " + ", ".join(change for change in changes if change)


def readable_text(value: str, name: str, max_chars: int, max_bytes: int | None = None) -> str:
    """The text as it will be kept: what a screen cannot show is replaced by a space.

    Line breaks stay, and so do the joiners some scripts need. What a person approves is this
    text, so nothing invisible can ride along with it.
    """
    shown = "".join(
        character if character.isprintable() or character == "\n" or character in _JOINERS else " "
        for character in value
    ).strip()
    if not shown or len(shown) > max_chars:
        raise ValidationFailedError(
            f"{name} must be 1 to {max_chars} characters.", max_chars=max_chars
        )
    if max_bytes is not None and len(shown.encode()) > max_bytes:
        raise ValidationFailedError(
            f"{name} is too long: at most {max_bytes} bytes as UTF-8.", max_bytes=max_bytes
        )
    return shown


def _key(value: str) -> str:
    plain = value.isascii() and all(
        character.isalnum() or character in _KEY_PUNCTUATION for character in value
    )
    if not 0 < len(value) <= KEY_MAX_CHARS or not plain:
        raise ValidationFailedError(
            f"idempotency_key must be 1 to {KEY_MAX_CHARS} letters, digits, hyphens or underscores."
        )
    return value


def _amount(value: int) -> int:
    if not 0 < value <= LARGEST_NUMBER:
        raise ValidationFailedError("amount_minor must be a positive whole number of paise.")
    return value


def _id(value: int, name: str) -> int:
    if value > LARGEST_NUMBER:
        raise ValidationFailedError(f"{name} is not a valid number.")
    return require_id(value, name)


class ProposalService:
    def __init__(
        self,
        tickets: Tickets,
        cases: RefundCases,
        actions: PendingActions,
        approved: "CarriesOut",
        limits: Limits,
        clock: Clock,
    ) -> None:
        self._tickets = tickets
        self._cases = cases
        self._actions = actions
        self._approved = approved
        self._limits = limits
        self._clock = clock

    async def refund(
        self,
        identity: Identity,
        request: RefundRequest,
        *,
        idempotency_key: str,
        approval_token: str | None = None,
    ) -> Proposal:
        identity.require(Permission.REFUNDS_REQUEST)
        refund = self._limits.refund
        args: dict[str, JsonValue] = {
            "order_id": _id(request.order_id, "order_id"),
            "amount_minor": _amount(request.amount_minor),
            "reason_code": request.reason_code,
            "reason_text": readable_text(
                request.reason_text, "reason_text", refund.reason_chars, refund.reason_bytes
            ),
        }
        if request.ticket_id is not None:
            args["ticket_id"] = _id(request.ticket_id, "ticket_id")
        if approval_token is not None:
            return await self._approved.carry_out(
                identity,
                ISSUE_REFUND,
                args,
                idempotency_key=_key(idempotency_key),
                approval_token=approval_token,
            )
        earlier = await self._earlier(identity, ISSUE_REFUND, _key(idempotency_key), args)
        if earlier is not None:
            return earlier
        now = self._clock()
        if request.ticket_id is not None:
            ticket = await self._ticket(identity, request.ticket_id, now)
            if ticket.order_id != request.order_id:
                raise ValidationFailedError("That ticket is about a different order.")
        case, snapshot = await self._assess(
            identity, request.order_id, request.amount_minor, request.reason_code, now
        )
        kind = refund_policy.approval_kind(request.amount_minor, case, self._limits.approval)
        return await self._propose(
            _Asked(ISSUE_REFUND, args, idempotency_key, request.ticket_id, request.order_id),
            identity,
            kind,
            snapshot,
            now,
        )

    async def ticket_update(
        self,
        identity: Identity,
        update: TicketUpdate,
        *,
        idempotency_key: str,
        approval_token: str | None = None,
    ) -> Proposal:
        identity.require(Permission.TICKETS_WRITE)
        args = self._update_args(update)
        if approval_token is not None:
            return await self._approved.carry_out(
                identity,
                UPDATE_TICKET,
                args,
                idempotency_key=_key(idempotency_key),
                approval_token=approval_token,
            )
        earlier = await self._earlier(identity, UPDATE_TICKET, _key(idempotency_key), args)
        if earlier is not None:
            return earlier
        now = self._clock()
        ticket = await self._ticket(identity, update.ticket_id, now)
        _check_move(ticket, update)
        if update.status == "resolved" and await self._cases.unfinished_on_ticket(
            scope_of(identity), ticket.id, now=now
        ):
            raise PolicyViolationError(
                "This ticket has a refund that is not finished yet, so it cannot be resolved.",
                rule="refund_in_progress",
            )
        snapshot: dict[str, JsonValue] = {"ticket_version": ticket.version}
        if update.recommendation is not None:
            advice = update.recommendation
            _, snapshot["recommendation"] = await self._assess(
                identity, ticket.order_id, advice.amount_minor, advice.reason_code, now
            )
        return await self._propose(
            _Asked(UPDATE_TICKET, args, idempotency_key, ticket.id, None),
            identity,
            "self",
            snapshot,
            now,
        )

    def _update_args(self, update: TicketUpdate) -> dict[str, JsonValue]:
        if update.status is None and update.note is None and update.escalate_to is None:
            raise ValidationFailedError("Give at least one of status, note or escalate_to.")
        if update.escalate_to is not None and (update.note is None or update.status is not None):
            raise ValidationFailedError("An escalation needs a note and cannot also set a status.")
        if update.recommendation is not None and update.escalate_to is None:
            raise ValidationFailedError("A recommendation is given only when escalating.")
        args: dict[str, JsonValue] = {"ticket_id": _id(update.ticket_id, "ticket_id")}
        if update.status is not None:
            args["status"] = update.status
        if update.note is not None:
            args["note"] = readable_text(update.note, "note", self._limits.ticket.note_write_chars)
        if update.escalate_to is not None:
            args["escalate_to"] = update.escalate_to
        if update.recommendation is not None:
            refund = self._limits.refund
            args["recommendation"] = {
                "amount_minor": _amount(update.recommendation.amount_minor),
                "reason_code": update.recommendation.reason_code,
                "reason_text": readable_text(
                    update.recommendation.reason_text,
                    "recommendation.reason_text",
                    refund.reason_chars,
                    refund.reason_bytes,
                ),
            }
        return args

    async def _ticket(self, identity: Identity, ticket_id: int, now: datetime) -> TicketRecord:
        found = await self._tickets.get(scope_of(identity), ticket_id, notes=0, now=now)
        if found is None:
            raise NotFoundError("No ticket with that number is visible to you.")
        return found.ticket

    async def _assess(
        self,
        identity: Identity,
        order_id: int,
        amount_minor: int,
        reason: RefundReason,
        now: datetime,
    ) -> tuple[RefundCase, dict[str, JsonValue]]:
        case = await self._cases.for_order(scope_of(identity), order_id, now=now)
        if case is None:
            raise NotFoundError("No order with that number is visible to you.")
        seen = refund_policy.assess(
            amount_minor, reason, case, now=now, goodwill=self._limits.goodwill
        )
        return case, seen

    async def _earlier(
        self, identity: Identity, tool: WriteTool, key: str, args: dict[str, JsonValue]
    ) -> Proposal | None:
        """The answer to a repeated request, so that a retry never asks twice."""
        found = await self._actions.find(scope_of(identity), tool, key)
        return None if found is None else self._answer(found, args_hash(tool, args))

    async def _propose(
        self,
        asked: "_Asked",
        identity: Identity,
        kind: ApprovalKind,
        snapshot: dict[str, JsonValue],
        now: datetime,
    ) -> Proposal:
        approval = self._limits.approval
        waits_s = (
            approval.pending_ttl_self_s if kind == "self" else approval.pending_ttl_supervisor_s
        )
        hashed = args_hash(asked.tool, asked.args)
        stored = await self._actions.propose(
            NewAction(
                tool=asked.tool,
                args=asked.args,
                args_hash=hashed,
                requester_oid=identity.oid,
                requester_name=identity.name,
                requester_region=scope_of(identity).region,
                approval_kind=kind,
                policy_snapshot=snapshot,
                idempotency_key=asked.key,
                ticket_id=asked.ticket_id,
                order_id=asked.order_id,
                expires_at=now + timedelta(seconds=waits_s),
            ),
            now=now,
        )
        if stored is None:
            refund_policy.refuse_while_waiting(asked.args.get("amount_minor"))
        return self._answer(stored, hashed)

    def _answer(self, action: ActionRecord, asked_hash: str) -> Proposal:
        if action.args_hash != asked_hash:
            raise IdempotencyConflictError(
                "This idempotency key was already used with different arguments."
            )
        status = _ANSWERS[action.status]
        if status == "approval_required" and action.expires_at <= self._clock():
            status = "expired"
        return Proposal(
            status=status,
            action_id=action.id,
            args_hash=action.args_hash,
            approval_kind=action.approval_kind,
            summary=summary_of(action.tool, action.args),
            expires_at=action.expires_at,
            result=written(action.result) if action.status == "executed" else None,
        )


@dataclass(frozen=True, slots=True)
class _Asked:
    tool: WriteTool
    args: dict[str, JsonValue]
    key: str
    ticket_id: int | None
    order_id: int | None


class CarriesOut(Protocol):
    """What a call that brings an approval is handed to."""

    async def carry_out(
        self,
        identity: Identity,
        tool: WriteTool,
        args: dict[str, JsonValue],
        *,
        idempotency_key: str,
        approval_token: str,
    ) -> Proposal: ...


def _check_move(ticket: TicketRecord, update: TicketUpdate) -> None:
    if update.status is not None and update.status not in STATUS_MOVES[ticket.status]:
        raise PolicyViolationError(
            f"A ticket that is {ticket.status} cannot be set to {update.status}.",
            rule="status_move_not_allowed",
            current=ticket.status,
            requested=update.status,
        )
    if update.escalate_to is not None and (ticket.status != "open" or ticket.queue != "L1"):
        raise PolicyViolationError(
            "Only an open ticket in the L1 queue can be escalated.",
            rule="cannot_escalate",
            current=ticket.status,
            queue=ticket.queue,
        )

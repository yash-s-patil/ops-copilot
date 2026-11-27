"""The second half of every write: carried out once, for exactly what was approved.

A write tool called with an approval token reaches this. The token is checked against the
stored action and against this call, the action is taken and the token used up, and the
rules are applied again as things stand now, because time has passed since a person looked.
"""

from datetime import datetime
from functools import partial

from opscp_common.errors import (
    ApprovalInvalidError,
    NotFoundError,
    OpsError,
    PolicyViolationError,
)
from opscp_common.hashing import JsonValue, args_hash
from opscp_common.limits import Limits
from opscp_common.vocabulary import ISSUE_REFUND
from opscp_mcp.authz.identity import Identity
from opscp_mcp.ports.actions import ActionRecord, PendingActions, WriteTool
from opscp_mcp.ports.controls import AuditEntry
from opscp_mcp.ports.execution import (
    ApprovedAction,
    Claim,
    EntryFor,
    Escalation,
    Executions,
    Ledger,
    NewNote,
    NewRefund,
    RefusedAtExecutionError,
    TicketChange,
)
from opscp_mcp.ports.signing import ApprovalSigner
from opscp_mcp.services import recording, refund_policy
from opscp_mcp.services.approvals import NEEDED_TO_ASK
from opscp_mcp.services.orders import Clock
from opscp_mcp.services.proposals import STATUS_MOVES, Proposal, summary_of, written
from opscp_mcp.services.scope import scope_of

_INVALID = "This approval cannot be used."
_OVER = ("executed", "failed")


def _number(args: dict[str, JsonValue], name: str) -> int:
    value = args.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ApprovalInvalidError(_INVALID)
    return value


class ExecutionService:
    def __init__(
        self,
        actions: PendingActions,
        executions: Executions,
        signer: ApprovalSigner,
        limits: Limits,
        clock: Clock,
    ) -> None:
        self._actions = actions
        self._executions = executions
        self._signer = signer
        self._limits = limits
        self._clock = clock

    async def carry_out(
        self,
        identity: Identity,
        tool: WriteTool,
        args: dict[str, JsonValue],
        *,
        idempotency_key: str,
        approval_token: str,
    ) -> Proposal:
        """`args` are this call's validated arguments, as a request for approval hashes them."""
        identity.require(NEEDED_TO_ASK[tool])
        asked_hash = args_hash(tool, args)
        action = await self._actions.find(scope_of(identity), tool, idempotency_key)
        if action is None or action.args_hash != asked_hash:
            raise ApprovalInvalidError(_INVALID)
        if action.status in _OVER:
            return _outcome(action)
        now = self._clock()
        claims = await self._signer.verify(approval_token)
        approved = action.status == "approved" and action.expires_at > now
        matches = claims is not None and (
            claims.action_id,
            claims.tool,
            claims.args_hash,
            claims.requester_oid,
            claims.approver_oid,
        ) == (action.id, tool, asked_hash, identity.oid, action.approver_oid)
        if claims is None or not matches or not approved or claims.expires_at <= now:
            raise ApprovalInvalidError(_INVALID)
        claim = Claim(
            action_id=action.id,
            token_id=claims.token_id,
            approver_oid=claims.approver_oid,
            args_hash=asked_hash,
            scope=scope_of(identity),
            order_id=_number(args, "order_id") if tool == ISSUE_REFUND else None,
            now=now,
        )
        result = await self._run(claim, tool)
        if result is not None:
            return _executed(action, result)
        # Another call took the action first. If it was this same call repeated, answer alike.
        latest = await self._actions.find(scope_of(identity), tool, idempotency_key)
        if latest is None or latest.status not in _OVER or latest.args_hash != asked_hash:
            raise ApprovalInvalidError(_INVALID)
        return _outcome(latest)

    async def _run(self, claim: Claim, tool: WriteTool) -> dict[str, JsonValue] | None:
        offer = recording.current()
        entry: EntryFor = _unrecorded if offer is None else offer.entry_for
        work = partial(self._refund, claim.now) if tool == ISSUE_REFUND else self._ticket_update
        try:
            executed = await self._executions.run(claim, work, entry)
        except RefusedAtExecutionError as refused:
            if offer is not None:
                offer.audit_id = refused.audit_id
            # Raised bare: the wrapper already holds the refusal, and each naming the other
            # as its cause would make a chain with no end.
            raise refused.refusal from None
        if executed is None:
            return None
        if offer is not None:
            offer.audit_id = executed.audit_id
        return executed.result

    async def _refund(
        self, now: datetime, action: ApprovedAction, ledger: Ledger
    ) -> dict[str, JsonValue]:
        args = action.args
        order_id, amount = _number(args, "order_id"), _number(args, "amount_minor")
        case = await ledger.refund_case(order_id)
        if case is None:
            raise NotFoundError("No order with that number is visible to you.")
        ticket_id = args.get("ticket_id")
        if isinstance(ticket_id, int):
            ticket = await ledger.ticket(ticket_id)
            if ticket is None or ticket.order_id != order_id:
                raise NotFoundError("No ticket with that number is visible to you.")
        reason = refund_policy.reason_of(args.get("reason_code"))
        refund_policy.assess(amount, reason, case, now=now, goodwill=self._limits.goodwill)
        needed = refund_policy.approval_kind(amount, case, self._limits.approval)
        if needed == "supervisor" and action.approval_kind != "supervisor":
            raise PolicyViolationError(
                "This refund now needs a supervisor's approval. Ask for it again.",
                rule="needs_supervisor",
            )
        refund_id = await ledger.add_refund(
            NewRefund(
                order_id=order_id,
                ticket_id=ticket_id if isinstance(ticket_id, int) else None,
                region=case.region,
                amount_minor=amount,
                reason_code=reason,
                reason_text=str(args.get("reason_text", "")),
            )
        )
        return {"refund_id": refund_id, "status": "approved", "amount_minor": amount}

    async def _ticket_update(self, action: ApprovedAction, ledger: Ledger) -> dict[str, JsonValue]:
        args = action.args
        ticket = await ledger.ticket(_number(args, "ticket_id"))
        if ticket is None:
            raise NotFoundError("No ticket with that number is visible to you.")
        seen_version = (action.policy_snapshot or {}).get("ticket_version")
        status = refund_policy.status_of(args.get("status"))
        escalating = args.get("escalate_to") is not None
        stale = ticket.version != seen_version
        if stale or (status is not None and status not in STATUS_MOVES[ticket.status]):
            raise PolicyViolationError(
                "The ticket has changed since this was approved. Look at it again.",
                rule="ticket_changed",
            )
        if escalating and (ticket.status != "open" or ticket.queue != "L1"):
            raise PolicyViolationError(
                "Only an open ticket in the L1 queue can be escalated.", rule="cannot_escalate"
            )
        if status == "resolved" and await ledger.refund_unfinished_on(ticket.id):
            raise PolicyViolationError(
                "This ticket has a refund that is not finished yet, so it cannot be resolved.",
                rule="refund_in_progress",
            )
        advice = args.get("recommendation")
        changed = await ledger.change_ticket(
            TicketChange(
                ticket_id=ticket.id,
                from_version=ticket.version,
                status=status,
                escalation=Escalation(
                    by_oid=action.approver_oid,
                    by_name=action.approver_name,
                    recommendation=advice if isinstance(advice, dict) else None,
                )
                if escalating
                else None,
            )
        )
        if changed is None:
            raise PolicyViolationError(
                "The ticket has changed since this was approved. Look at it again.",
                rule="ticket_changed",
            )
        note = args.get("note")
        if isinstance(note, str):
            await ledger.add_note(
                NewNote(
                    ticket_id=ticket.id,
                    author_oid=action.approver_oid,
                    author_name=action.approver_name,
                    body=note,
                )
            )
        return {
            "ticket_id": changed.id,
            "status": changed.status,
            "queue": changed.queue,
            "version": changed.version,
        }


def _unrecorded(_failure: OpsError | None) -> AuditEntry:
    raise RuntimeError("A write was carried out with no audit entry offered for it")


def _outcome(action: ActionRecord) -> Proposal:
    """The answer to a call whose action is already over: what was done, or that it failed."""
    if action.status == "executed":
        return _executed(action, action.result)
    return _executed(action, None).model_copy(update={"status": "failed"})


def _executed(action: ActionRecord, result: dict[str, JsonValue] | None) -> Proposal:
    return Proposal(
        status="executed",
        action_id=action.id,
        args_hash=action.args_hash,
        approval_kind=action.approval_kind,
        summary=summary_of(action.tool, action.args),
        expires_at=action.expires_at,
        result=written(result),
    )

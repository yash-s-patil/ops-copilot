"""A person's decision on a pending action, and the token that carries an approval.

Nothing here is reachable from a tool. An approval comes only from an HTTP route that sits
behind a person's click, and it is tied to the exact arguments that person was shown.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from opscp_common.errors import ApprovalInvalidError, NotFoundError, PermissionDeniedError
from opscp_common.hashing import JsonValue
from opscp_common.limits import Limits
from opscp_common.vocabulary import ALL_REGIONS
from opscp_mcp.authz.identity import Identity
from opscp_mcp.authz.policy import Permission
from opscp_mcp.ports.actions import (
    ActionRecord,
    ActionStatus,
    ActionsWanted,
    ApprovalKind,
    Decision,
    PendingActions,
    WriteTool,
)
from opscp_mcp.ports.controls import AuditEntry
from opscp_mcp.ports.signing import ApprovalClaims, ApprovalSigner
from opscp_mcp.services.caps import Result
from opscp_mcp.services.inputs import require_id
from opscp_mcp.services.orders import Clock
from opscp_mcp.services.proposals import readable_text, summary_of

type EntryNow = Callable[[], AuditEntry]

NEEDED_TO_ASK: dict[WriteTool, Permission] = {
    "update_ticket": Permission.TICKETS_WRITE,
    "issue_refund": Permission.REFUNDS_REQUEST,
}
_NOT_FOUND = "No pending action with that id is visible to you."
_SHOWN_TO_SUPERVISORS = ("proposed", "approved")


class ApprovalToken(Result):
    token: str
    expires_at: datetime


class ActionView(Result):
    action_id: UUID
    tool: WriteTool
    status: ActionStatus
    approval_kind: ApprovalKind
    summary: str
    args: dict[str, JsonValue]
    args_hash: str
    policy_snapshot: dict[str, JsonValue] | None
    ticket_id: int | None
    requester_oid: UUID
    requester_name: str
    approver_name: str | None
    decided_at: datetime | None
    decision_reason: str | None
    created_at: datetime
    expires_at: datetime
    mine: bool
    may_decide: bool


class Approved(Result):
    action: ActionView
    approval: ApprovalToken | None
    """Given only to the requester. A supervisor's approval hands nobody a token."""


@dataclass(frozen=True, slots=True)
class Recorded[T]:
    """What a decision produced, and the number of the audit entry written with it."""

    outcome: T
    audit_id: int


def _decides_for(identity: Identity, action: ActionRecord) -> bool:
    """Whether a supervisor's approval of this action is this person's to give."""
    return (
        action.approval_kind == "supervisor"
        and identity.can(Permission.REFUNDS_APPROVE)
        and identity.oid != action.requester_oid
        and identity.region in (ALL_REGIONS, action.requester_region)
    )


def _may_see(identity: Identity, action: ActionRecord, now: datetime) -> bool:
    """Its requester always; a supervisor only while it waits for one or stands approved."""
    live = action.status in _SHOWN_TO_SUPERVISORS and action.expires_at > now
    return identity.oid == action.requester_oid or (live and _decides_for(identity, action))


class ApprovalService:
    def __init__(
        self, actions: PendingActions, signer: ApprovalSigner, limits: Limits, clock: Clock
    ) -> None:
        self._actions = actions
        self._signer = signer
        self._limits = limits
        self._clock = clock

    async def get(self, identity: Identity, action_id: UUID) -> ActionView:
        return self._view(identity, await self._visible(identity, action_id))

    async def waiting(self, identity: Identity, ticket_id: int | None) -> tuple[ActionView, ...]:
        """What the caller asked for and has not seen through, and what waits for them."""
        identity.require_a_role()
        if ticket_id is not None:
            require_id(ticket_id, "ticket_id")
        wanted = ActionsWanted(
            requester_oid=identity.oid,
            awaiting_supervisor_in=(
                identity.region if identity.can(Permission.REFUNDS_APPROVE) else None
            ),
            ticket_id=ticket_id,
            limit=self._limits.approval.list_max,
        )
        found = await self._actions.waiting(wanted, now=self._clock())
        return tuple(
            self._view(identity, action)
            for action in found
            if _may_see(identity, action, self._clock())
        )

    async def approve(
        self, identity: Identity, action_id: UUID, args_hash: str, entry: EntryNow
    ) -> Recorded[Approved]:
        """Approves what the person was shown, and nothing else.

        `args_hash` is the fingerprint on the card they were looking at. If the stored
        action differs, the approval is refused. `entry` gives the audit entry, which is
        written with the approval in one transaction, so there is never one without the other.
        """
        action = await self._visible(identity, action_id)
        now = self._clock()
        self._still_proposed(action, now)
        if args_hash != action.args_hash:
            raise ApprovalInvalidError("This is not the action you were shown.")
        self._may_approve(identity, action)
        mine = identity.oid == action.requester_oid
        # Signed before the decision is stored: if signing fails, the action stays proposed.
        token = await self._mint(action, identity.oid, now) if mine else None
        approved = await self._actions.decide(
            action_id,
            Decision(
                status="approved",
                seen_hash=args_hash,
                approver_oid=identity.oid,
                approver_name=identity.name,
                reason=None,
                decided_at=now,
                expires_at=now + timedelta(seconds=self._limits.approval.approved_ttl_s),
            ),
            entry(),
        )
        if approved is None:
            raise ApprovalInvalidError("This action has already been decided or has expired.")
        outcome = Approved(action=self._view(identity, approved.action), approval=token)
        return Recorded(outcome, approved.audit_id)

    async def reject(
        self, identity: Identity, action_id: UUID, reason: str, entry: EntryNow
    ) -> Recorded[ActionView]:
        """The requester may withdraw their own request; a supervisor may refuse another's."""
        action = await self._visible(identity, action_id)
        now = self._clock()
        self._still_proposed(action, now)
        why = readable_text(reason, "reason", self._limits.approval.decision_reason_chars)
        rejected = await self._actions.decide(
            action_id,
            Decision(
                status="rejected",
                seen_hash=action.args_hash,
                approver_oid=identity.oid,
                approver_name=identity.name,
                reason=why,
                decided_at=now,
                expires_at=action.expires_at,
            ),
            entry(),
        )
        if rejected is None:
            raise ApprovalInvalidError("This action has already been decided or has expired.")
        return Recorded(self._view(identity, rejected.action), rejected.audit_id)

    async def token(self, identity: Identity, action_id: UUID) -> ApprovalToken:
        """A fresh token for an action that is approved, for the person who asked for it."""
        action = await self._visible(identity, action_id)
        now = self._clock()
        if identity.oid != action.requester_oid:
            raise PermissionDeniedError("Only the person who asked for this may carry it out.")
        if action.status != "approved" or action.expires_at <= now:
            raise ApprovalInvalidError("This action is not approved, or its approval has lapsed.")
        identity.require(NEEDED_TO_ASK[action.tool])
        if action.approver_oid is None:
            raise ApprovalInvalidError("This action is not approved.")
        return await self._mint(action, action.approver_oid, now)

    async def _visible(self, identity: Identity, action_id: UUID) -> ActionRecord:
        identity.require_a_role()
        action = await self._actions.get(action_id)
        if action is None or not _may_see(identity, action, self._clock()):
            raise NotFoundError(_NOT_FOUND)
        return action

    def _still_proposed(self, action: ActionRecord, now: datetime) -> None:
        if action.status != "proposed":
            raise ApprovalInvalidError("This action has already been decided.")
        if action.expires_at <= now:
            raise ApprovalInvalidError("This action has expired. Ask for it again.")

    def _may_approve(self, identity: Identity, action: ActionRecord) -> None:
        if action.approval_kind == "self":
            if identity.oid != action.requester_oid:
                raise PermissionDeniedError("Only the person who asked for this may confirm it.")
            identity.require(NEEDED_TO_ASK[action.tool])
        elif not _decides_for(identity, action):
            raise PermissionDeniedError(
                "This needs a supervisor who is not the person who asked for it."
            )

    async def _mint(self, action: ActionRecord, approver: UUID, now: datetime) -> ApprovalToken:
        # Never beyond the approval itself: a token must not outlive what it carries.
        lifetime = timedelta(seconds=self._limits.approval.token_ttl_s)
        expires_at = min(now + lifetime, max(action.expires_at, now))
        claims = ApprovalClaims(
            token_id=uuid4(),
            requester_oid=action.requester_oid,
            approver_oid=approver,
            action_id=action.id,
            tool=action.tool,
            args_hash=action.args_hash,
            issued_at=now,
            expires_at=expires_at,
        )
        return ApprovalToken(token=await self._signer.sign(claims), expires_at=expires_at)

    def _view(self, identity: Identity, action: ActionRecord) -> ActionView:
        now = self._clock()
        live = action.status in ("proposed", "approved")
        status: ActionStatus = "expired" if live and action.expires_at <= now else action.status
        mine = identity.oid == action.requester_oid
        deciding = status == "proposed" and (
            (action.approval_kind == "self" and mine) or _decides_for(identity, action)
        )
        return ActionView(
            action_id=action.id,
            tool=action.tool,
            status=status,
            approval_kind=action.approval_kind,
            summary=summary_of(action.tool, action.args),
            args=action.args,
            args_hash=action.args_hash,
            policy_snapshot=action.policy_snapshot,
            ticket_id=action.ticket_id,
            requester_oid=action.requester_oid,
            requester_name=action.requester_name,
            approver_name=action.approver_name,
            decided_at=action.decided_at,
            decision_reason=action.decision_reason,
            created_at=action.created_at,
            expires_at=action.expires_at,
            mine=mine,
            may_decide=deciding,
        )

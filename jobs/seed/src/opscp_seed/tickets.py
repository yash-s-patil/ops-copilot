"""A support ticket and its conversation, written for an order that has a problem."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from random import Random

from opscp_common.limits import GoodwillLimits
from opscp_common.vocabulary import Priority, Queue, Region, TicketStatus
from opscp_seed.issues import FOLLOW_UPS, PENDING_QUESTIONS
from opscp_seed.journeys import SECONDS_PER_HOUR, OrderCase, Problem
from opscp_seed.model import AuthorKind, Engineer, Json, Ticket, TicketNote

ESCALATED_SHARE = 0.2
REPLY_SHARE = 0.25
FOLLOW_UP_SHARE = 0.2
RECOMMENDED_GOODWILL_PERCENT = 10
PERCENT = 100
RECENT_PROBLEM = timedelta(days=10)
LONGEST_WAIT_BEFORE_CONTACT = timedelta(hours=48)

_STATUSES: tuple[TicketStatus, ...] = ("open", "pending_customer", "resolved", "closed")
_ONGOING_WEIGHTS = (95, 5, 0, 0)
_RECENT_WEIGHTS = (75, 15, 10, 0)
_OLD_WEIGHTS = (0, 0, 60, 40)
_RAISED: Mapping[Priority, Priority] = {"P1": "P1", "P2": "P1", "P3": "P2", "P4": "P3"}
_REFUND_REASON_TEXT = {
    "lost_in_transit": "Loss in transit confirmed by the carrier",
    "damaged": "Damage reported by the customer",
}


@dataclass(frozen=True, slots=True, kw_only=True)
class Staff:
    l1: Mapping[Region, tuple[Engineer, ...]]
    l2: Mapping[Region, tuple[Engineer, ...]]


def recommend_refund(
    case: OrderCase, problem: Problem, now: datetime, goodwill: GoodwillLimits
) -> Json | None:
    """What an escalating engineer would suggest, kept inside the refund policy."""
    reason = problem.issue.refund_reason
    if reason is None:
        return None
    if reason != "goodwill_delay":
        return {
            "amount_minor": case.order.amount_minor,
            "reason_code": reason,
            "reason_text": _REFUND_REASON_TEXT[reason],
        }
    rule = goodwill.premium if case.customer.tier == "premium" else goodwill.standard
    delay_hours = int((now - problem.since).total_seconds() // SECONDS_PER_HOUR)
    if delay_hours < rule.min_delay_hours:
        return None
    percent = min(RECOMMENDED_GOODWILL_PERCENT, rule.max_percent)
    return {
        "amount_minor": case.order.amount_minor * percent // PERCENT,
        "reason_code": reason,
        "reason_text": f"Delayed {delay_hours} hours",
    }


class TicketWriter:
    def __init__(self, rng: Random, now: datetime, goodwill: GoodwillLimits, staff: Staff) -> None:
        self._rng = rng
        self._now = now
        self._goodwill = goodwill
        self._staff = staff

    def write(
        self, ticket_id: int, case: OrderCase, problem: Problem
    ) -> tuple[Ticket, tuple[TicketNote, ...]]:
        issue, region = problem.issue, case.order.region
        status = self._status(problem)
        escalated = self._rng.random() < ESCALATED_SHARE
        first_line = self._rng.choice(self._staff.l1[region])
        owner = self._rng.choice(self._staff.l2[region]) if escalated else first_line
        conversation = _Conversation(self._rng, self._now, ticket_id)

        created_at = self._created_at(problem.since)
        conversation.customer(case.customer.full_name, self._rng.choice(issue.openers), created_at)
        if self._rng.random() < REPLY_SHARE:
            conversation.engineer(first_line, self._rng.choice(issue.replies))
        if self._rng.random() < FOLLOW_UP_SHARE:
            conversation.customer(case.customer.full_name, self._rng.choice(FOLLOW_UPS))
        escalated_at = None
        if escalated:
            escalated_at = conversation.engineer(first_line, self._rng.choice(issue.escalations))
        if status == "pending_customer":
            conversation.engineer(owner, self._rng.choice(PENDING_QUESTIONS))
        if status in ("resolved", "closed"):
            conversation.engineer(owner, self._rng.choice(issue.resolutions))

        queue: Queue = "L2" if escalated else "L1"
        recommendation = None
        if escalated and status == "open":
            recommendation = recommend_refund(case, problem, self._now, self._goodwill)
        ticket = Ticket(
            id=ticket_id,
            order_id=case.order.id,
            customer_id=case.customer.id,
            region=region,
            status=status,
            priority=_RAISED[issue.priority] if case.customer.tier == "premium" else issue.priority,
            queue=queue,
            escalated_by_oid=first_line.oid if escalated else None,
            escalated_by_name=first_line.name if escalated else None,
            escalated_at=escalated_at,
            recommendation=recommendation,
            summary=self._rng.choice(issue.summaries),
            version=1 + int(escalated) + int(status != "open"),
            created_at=created_at,
            updated_at=conversation.last_at,
        )
        return ticket, tuple(conversation.notes)

    def _status(self, problem: Problem) -> TicketStatus:
        if problem.issue.ongoing:
            weights = _ONGOING_WEIGHTS
        elif self._now - problem.since < RECENT_PROBLEM:
            weights = _RECENT_WEIGHTS
        else:
            weights = _OLD_WEIGHTS
        return self._rng.choices(_STATUSES, weights)[0]

    def _created_at(self, problem_since: datetime) -> datetime:
        wait = min(LONGEST_WAIT_BEFORE_CONTACT, (self._now - problem_since) / 2)
        return (problem_since + wait * self._rng.uniform(0.2, 1)).replace(microsecond=0)


class _Conversation:
    """Notes on one ticket, each later than the one before and none in the future."""

    def __init__(self, rng: Random, now: datetime, ticket_id: int) -> None:
        self._rng = rng
        self._now = now
        self._ticket_id = ticket_id
        self.notes: list[TicketNote] = []

    @property
    def last_at(self) -> datetime:
        return self.notes[-1].created_at

    def customer(self, name: str, body: str, at: datetime | None = None) -> datetime:
        return self._add("customer", None, name, body, at)

    def engineer(self, author: Engineer, body: str) -> datetime:
        return self._add("engineer", author, author.name, body, None)

    def _add(
        self,
        kind: AuthorKind,
        author: Engineer | None,
        name: str,
        body: str,
        at: datetime | None,
    ) -> datetime:
        if at is None:
            at = self.last_at + (self._now - self.last_at) * self._rng.uniform(0.1, 0.6)
        self.notes.append(
            TicketNote(
                ticket_id=self._ticket_id,
                author_kind=kind,
                author_oid=author.oid if author else None,
                author_name=name,
                body=body,
                created_at=at.replace(microsecond=0),
            )
        )
        return self.last_at

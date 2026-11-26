from datetime import datetime
from uuid import UUID

import pytest

from opscp_common.errors import PermissionDeniedError
from opscp_common.limits import Limits
from opscp_mcp.authz.identity import Identity
from opscp_mcp.authz.policy import Role
from opscp_mcp.ports.meridian import (
    NoteRecord,
    RecommendationRecord,
    RefundRecord,
    Scope,
    TicketPageRecord,
    TicketPageRequest,
    TicketRecord,
    TicketWithNotes,
)
from opscp_mcp.services.caps import cap_text
from opscp_mcp.services.inputs import MAX_ID
from opscp_mcp.services.queues import VIEWS, filter_for
from opscp_mcp.services.tickets import TicketQuery, TicketService
from opscp_mcp_testing.cases.tickets import TicketCases
from opscp_mcp_testing.identities import ARJUN, KAVITA, PRIYA
from opscp_mcp_testing.world import LONG_NOTE, NOW

WORST_CASE_CHARACTER = "\u0939"


class TestTicketsInMemory(TicketCases):
    pass


def test_the_four_views_are_fixed() -> None:
    assert VIEWS == ("my_queue", "all_open", "waiting", "done")


def test_the_highest_role_decides_the_queue() -> None:
    both = PRIYA.model_copy(update={"roles": frozenset({Role.L1, Role.L2})})
    all_three = KAVITA.model_copy(update={"roles": frozenset(Role) - {Role.ADMIN}})

    assert filter_for("my_queue", both) == filter_for("my_queue", ARJUN)
    assert filter_for("waiting", all_three) is None


def test_someone_without_a_support_role_has_no_queue() -> None:
    nobody = Identity(oid=UUID(int=1), name="x", roles=frozenset(), region="west")

    with pytest.raises(PermissionDeniedError):
        filter_for("my_queue", nobody)


@pytest.mark.parametrize(
    ("text", "max_chars", "max_bytes", "expected", "truncated"),
    [
        ("hello", 10, 10, "hello", False),
        ("hello", 3, 10, "hel", True),
        ("hello", 10, 4, "hell", True),
        (LONG_NOTE[:6], 10, 7, LONG_NOTE[:2], True),
        ("", 10, 10, "", False),
        ("a\x00b\tc\nd", 10, 10, "a b c\nd", False),
    ],
)
def test_text_is_capped_by_characters_and_by_bytes(
    text: str, max_chars: int, max_bytes: int, expected: str, truncated: bool
) -> None:
    capped = cap_text(text, max_chars, max_bytes)

    assert (capped.text, capped.truncated) == (expected, truncated)


def _fullest_ticket(text: str) -> TicketRecord:
    return TicketRecord(
        id=MAX_ID,
        order_id=MAX_ID,
        customer_id=MAX_ID,
        customer_name=text,
        customer_tier="standard",
        region="south",
        status="pending_customer",
        priority="P3",
        queue="L2",
        summary=text,
        escalated_by_name=text,
        escalated_at=NOW,
        recommendation=RecommendationRecord(
            amount_minor=MAX_ID, reason_code="lost_in_transit", reason_text=text
        ),
        version=MAX_ID,
        updated_at=NOW.replace(microsecond=123456),
        refund_request=None,
        latest_refund=RefundRecord(
            id=MAX_ID,
            status="sent_to_payments",
            amount_minor=MAX_ID,
            currency="INR",
            reason_code="lost_in_transit",
            requested_by_name=text,
            approved_by_name=text,
            failure_reason=text,
        ),
    )


class _Fullest:
    """Every field as large as it can be, with all text in a three-byte script."""

    def __init__(self) -> None:
        self._text = WORST_CASE_CHARACTER * 5000

    async def get(
        self, scope: Scope, ticket_id: int, *, notes: int, now: datetime
    ) -> TicketWithNotes:
        note = NoteRecord(
            author_kind="agent_on_behalf", author_name=self._text, body=self._text, created_at=NOW
        )
        return TicketWithNotes(ticket=_fullest_ticket(self._text), notes=(note,) * notes)

    async def page(
        self, scope: Scope, request: TicketPageRequest, *, now: datetime
    ) -> TicketPageRecord:
        return TicketPageRecord(
            tickets=(_fullest_ticket(self._text),) * request.limit,
            counts=dict.fromkeys(request.counted, request.count_cap),
        )


async def test_the_largest_ticket_fits_in_one_result(limits: Limits) -> None:
    service = TicketService(_Fullest(), limits, lambda: NOW)

    view = await service.get(ARJUN, 1, notes=limits.notes.max)

    assert len(view.notes) == limits.notes.max
    assert len(view.model_dump_json().encode()) < limits.tool.result_bytes_max


async def test_the_largest_page_of_tickets_fits_in_one_result(limits: Limits) -> None:
    service = TicketService(_Fullest(), limits, lambda: NOW)

    page = await service.list(
        ARJUN, TicketQuery(view="all_open", page_size=limits.tickets.page_size_max)
    )

    assert len(page.items) == limits.tickets.page_size_max
    assert len(page.model_dump_json().encode()) < limits.tool.result_bytes_max

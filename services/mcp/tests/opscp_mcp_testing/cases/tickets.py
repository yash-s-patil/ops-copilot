"""How listing and reading tickets must behave, whichever backend holds the rows."""

import base64
import json

import pytest

from opscp_common.errors import NotFoundError, PermissionDeniedError, ValidationFailedError
from opscp_common.limits import Limits
from opscp_mcp.authz.identity import Identity
from opscp_mcp.services.queues import View
from opscp_mcp.services.tickets import TicketPage, TicketQuery, TicketService
from opscp_mcp_testing.cases.backend import Backend
from opscp_mcp_testing.identities import ARJUN, KAVITA, PRIYA, ROHAN, SAM
from opscp_mcp_testing.world import (
    APPROVED_BY_SUPERVISOR,
    ASHA,
    AWAITING_SUPERVISOR,
    BALA,
    CHITRA,
    CHITRA_ORDER,
    CLOSED,
    ESCALATED_BY_PRIYA,
    LONG_NOTE,
    NO_TICKET,
    NOW,
    PAID,
    PAYMENT_FAILED,
    PLAIN,
    REQUEST_EXPIRED,
    REQUESTED_BY_KAVITA,
    RESOLVED,
    SELF_APPROVAL_PENDING,
    SENT_TO_PAYMENTS,
    SOUTH_AWAITING_SUPERVISOR,
    SOUTH_ORDER,
    SOUTH_PLAIN,
    SOUTH_RESOLVED,
    STUCK_ON_ADDRESS,
    WAITING_ON_CUSTOMER,
    ago,
)

WEST_OPEN = [
    PLAIN,
    WAITING_ON_CUSTOMER,
    ESCALATED_BY_PRIYA,
    AWAITING_SUPERVISOR,
    SENT_TO_PAYMENTS,
    APPROVED_BY_SUPERVISOR,
    REQUEST_EXPIRED,
    REQUESTED_BY_KAVITA,
    SELF_APPROVAL_PENDING,
    PAYMENT_FAILED,
]
WEST_DONE = [PAID, RESOLVED, CLOSED]
EVERY_OPEN = [
    PLAIN,
    WAITING_ON_CUSTOMER,
    SOUTH_PLAIN,
    ESCALATED_BY_PRIYA,
    AWAITING_SUPERVISOR,
    SENT_TO_PAYMENTS,
    APPROVED_BY_SUPERVISOR,
    REQUEST_EXPIRED,
    REQUESTED_BY_KAVITA,
    SELF_APPROVAL_PENDING,
    PAYMENT_FAILED,
    SOUTH_AWAITING_SUPERVISOR,
]
VIEW_TABLE: list[tuple[Identity, View, list[int]]] = [
    (PRIYA, "my_queue", [PLAIN, WAITING_ON_CUSTOMER, SELF_APPROVAL_PENDING]),
    (PRIYA, "all_open", WEST_OPEN),
    (PRIYA, "waiting", [ESCALATED_BY_PRIYA]),
    (PRIYA, "done", WEST_DONE),
    (
        ARJUN,
        "my_queue",
        [ESCALATED_BY_PRIYA, APPROVED_BY_SUPERVISOR, REQUEST_EXPIRED, PAYMENT_FAILED],
    ),
    (ARJUN, "all_open", WEST_OPEN),
    (ARJUN, "waiting", [AWAITING_SUPERVISOR, SENT_TO_PAYMENTS, REQUESTED_BY_KAVITA]),
    (ARJUN, "done", WEST_DONE),
    (KAVITA, "my_queue", [AWAITING_SUPERVISOR, SOUTH_AWAITING_SUPERVISOR]),
    (KAVITA, "all_open", EVERY_OPEN),
    (KAVITA, "waiting", []),
    (KAVITA, "done", [PAID, SOUTH_RESOLVED, RESOLVED, CLOSED]),
    (ROHAN, "my_queue", [SOUTH_PLAIN]),
    (ROHAN, "all_open", [SOUTH_PLAIN, SOUTH_AWAITING_SUPERVISOR]),
    (ROHAN, "waiting", []),
    (ROHAN, "done", [SOUTH_RESOLVED]),
]
WHOLE_PAGE = 25


def _forged(position: dict[str, object]) -> str:
    return base64.urlsafe_b64encode(json.dumps(position).encode()).decode()


def _ids(page: TicketPage) -> list[int]:
    return [item.ticket_id for item in page.items]


class TicketCases:
    @pytest.fixture
    def service(self, backend: Backend, limits: Limits) -> TicketService:
        return TicketService(backend.tickets, limits, lambda: NOW)

    @pytest.mark.parametrize(
        ("identity", "view", "expected"),
        VIEW_TABLE,
        ids=[f"{identity.name}:{view}" for identity, view, _ in VIEW_TABLE],
    )
    async def test_each_view_holds_the_tickets_its_role_should_see_newest_first(
        self, service: TicketService, identity: Identity, view: View, expected: list[int]
    ) -> None:
        page = await service.list(identity, TicketQuery(view=view, page_size=WHOLE_PAGE))

        assert _ids(page) == expected
        assert page.next_cursor is None

    @pytest.mark.parametrize("identity", [PRIYA, ARJUN, KAVITA, ROHAN], ids=lambda who: who.name)
    async def test_every_answer_carries_the_size_of_all_four_views(
        self, service: TicketService, identity: Identity
    ) -> None:
        page = await service.list(identity, TicketQuery(view="done", page_size=1))

        assert page.counts == {
            view: len(expected) for who, view, expected in VIEW_TABLE if who is identity
        }

    async def test_a_count_stops_at_its_cap(self, backend: Backend, limits: Limits) -> None:
        capped = limits.model_copy(
            update={"tickets": limits.tickets.model_copy(update={"count_max": 2})}
        )
        service = TicketService(backend.tickets, capped, lambda: NOW)

        page = await service.list(PRIYA, TicketQuery(view="waiting"))

        assert page.counts == {"my_queue": 2, "all_open": 2, "waiting": 1, "done": 2}

    async def test_pages_follow_one_another_without_gaps_or_repeats(
        self, service: TicketService
    ) -> None:
        seen: list[int] = []
        cursor = None
        for _ in range(len(WEST_OPEN)):
            page = await service.list(
                PRIYA, TicketQuery(view="all_open", page_size=4, cursor=cursor)
            )
            seen += _ids(page)
            cursor = page.next_cursor
            if cursor is None:
                break

        assert seen == WEST_OPEN

    async def test_the_default_page_size_comes_from_the_limits(
        self, service: TicketService, limits: Limits
    ) -> None:
        page = await service.list(KAVITA, TicketQuery(view="all_open"))

        assert len(page.items) == limits.tickets.page_size_default
        assert page.next_cursor is not None

    async def test_tickets_can_be_found_by_order_or_customer_without_a_view(
        self, service: TicketService
    ) -> None:
        by_order = await service.list(PRIYA, TicketQuery(order_id=STUCK_ON_ADDRESS))
        by_customer = await service.list(PRIYA, TicketQuery(customer_id=ASHA))

        assert _ids(by_order) == [PLAIN]
        assert _ids(by_customer) == [PLAIN, WAITING_ON_CUSTOMER, RESOLVED]

    async def test_a_view_can_be_narrowed_by_order_customer_and_status(
        self, service: TicketService
    ) -> None:
        narrowed = await service.list(
            PRIYA, TicketQuery(view="my_queue", customer_id=ASHA, status="pending_customer")
        )
        contradictory = await service.list(PRIYA, TicketQuery(view="done", status="open"))
        another_order = await service.list(
            ARJUN, TicketQuery(view="waiting", order_id=STUCK_ON_ADDRESS)
        )

        assert _ids(narrowed) == [WAITING_ON_CUSTOMER]
        assert _ids(contradictory) == []
        assert _ids(another_order) == []

    async def test_asking_for_everything_is_refused(self, service: TicketService) -> None:
        with pytest.raises(ValidationFailedError, match="view"):
            await service.list(PRIYA, TicketQuery())
        with pytest.raises(ValidationFailedError, match="view"):
            await service.list(PRIYA, TicketQuery(status="open"))

    @pytest.mark.parametrize("page_size", [0, -1, 26])
    async def test_a_page_size_outside_the_limits_is_refused(
        self, service: TicketService, page_size: int
    ) -> None:
        with pytest.raises(ValidationFailedError, match="page_size"):
            await service.list(PRIYA, TicketQuery(view="all_open", page_size=page_size))

    @pytest.mark.parametrize(
        "cursor",
        [
            "",
            "not base64 !",
            "bm90IGpzb24=",
            _forged({"id": 1}),
            _forged({"updated_at": "2026-11-28T04:30:00", "id": 1}),
            _forged({"updated_at": "9999-12-31T23:59:59-23:59", "id": 1}),
            _forged({"updated_at": "2026-11-28T04:30:00Z", "id": 2**63}),
            _forged({"updated_at": "2026-11-28T04:30:00Z", "id": -1}),
            _forged({"updated_at": "2026-11-28T04:30:00Z", "id": 1, "note": "x" * 500}),
        ],
    )
    async def test_a_cursor_this_server_did_not_issue_is_refused(
        self, service: TicketService, cursor: str
    ) -> None:
        with pytest.raises(ValidationFailedError, match="cursor"):
            await service.list(PRIYA, TicketQuery(view="all_open", cursor=cursor))

    async def test_a_forged_cursor_only_moves_within_what_the_caller_may_list(
        self, service: TicketService
    ) -> None:
        far_future = _forged({"updated_at": "2999-01-01T00:00:00Z", "id": 2**62})

        page = await service.list(
            PRIYA, TicketQuery(view="all_open", cursor=far_future, page_size=WHOLE_PAGE)
        )

        assert _ids(page) == WEST_OPEN

    async def test_tickets_of_another_region_are_not_listed(self, service: TicketService) -> None:
        by_order = await service.list(PRIYA, TicketQuery(order_id=SOUTH_ORDER))
        by_customer = await service.list(PRIYA, TicketQuery(customer_id=BALA))

        assert (_ids(by_order), _ids(by_customer)) == ([], [])

    async def test_a_list_item_carries_what_an_inbox_row_shows(
        self, service: TicketService
    ) -> None:
        page = await service.list(PRIYA, TicketQuery(view="waiting"))

        assert [item.model_dump() for item in page.items] == [
            {
                "ticket_id": ESCALATED_BY_PRIYA,
                "order_id": CHITRA_ORDER,
                "status": "open",
                "priority": "P3",
                "queue": "L2",
                "summary": "A test ticket",
                "summary_truncated": False,
                "customer_name": "Chitra Desai",
                "customer_tier": "standard",
                "escalated_by_name": "Priya Nair",
                "has_recommendation": True,
                "refund_state": None,
                "needs_my_approval": False,
                "updated_at": ago(4),
            }
        ]

    async def test_each_ticket_reports_one_refund_state(self, service: TicketService) -> None:
        page = await service.list(ARJUN, TicketQuery(view="all_open", page_size=WHOLE_PAGE))
        done = await service.list(ARJUN, TicketQuery(view="done"))

        states = {item.ticket_id: item.refund_state for item in (*page.items, *done.items)}
        assert states == {
            PLAIN: None,
            WAITING_ON_CUSTOMER: None,
            ESCALATED_BY_PRIYA: None,
            AWAITING_SUPERVISOR: "waiting_for_supervisor",
            SENT_TO_PAYMENTS: "sent_to_payments",
            APPROVED_BY_SUPERVISOR: "approved_by_supervisor",
            REQUEST_EXPIRED: None,
            REQUESTED_BY_KAVITA: "waiting_for_supervisor",
            SELF_APPROVAL_PENDING: None,
            PAYMENT_FAILED: "failed",
            PAID: "paid",
            RESOLVED: None,
            CLOSED: None,
        }

    async def test_only_a_supervisor_who_did_not_ask_is_told_to_approve(
        self, service: TicketService
    ) -> None:
        as_supervisor = await service.list(
            KAVITA, TicketQuery(view="all_open", page_size=WHOLE_PAGE)
        )
        as_requester = await service.list(ARJUN, TicketQuery(view="waiting"))

        assert [item.ticket_id for item in as_supervisor.items if item.needs_my_approval] == [
            AWAITING_SUPERVISOR,
            SOUTH_AWAITING_SUPERVISOR,
        ]
        assert not any(item.needs_my_approval for item in as_requester.items)

    async def test_a_ticket_is_returned_with_its_recommendation_and_newest_notes(
        self, service: TicketService, limits: Limits
    ) -> None:
        view = await service.get(ARJUN, ESCALATED_BY_PRIYA, notes=2)

        assert view.model_dump(exclude={"notes"}) == {
            "ticket_id": ESCALATED_BY_PRIYA,
            "order_id": CHITRA_ORDER,
            "customer_id": CHITRA,
            "status": "open",
            "priority": "P3",
            "queue": "L2",
            "summary": "A test ticket",
            "summary_truncated": False,
            "escalated_by_name": "Priya Nair",
            "escalated_at": ago(4),
            "recommendation": {
                "amount_minor": 890000,
                "reason_code": "lost_in_transit",
                "reason_text": "Loss confirmed by the carrier",
                "reason_text_truncated": False,
            },
            "version": 2,
            "updated_at": ago(4),
            "needs_my_approval": False,
            "refund": None,
        }
        newest, older = view.notes
        assert (older.author_kind, older.author_name, older.truncated, older.created_at) == (
            "engineer",
            "Priya Nair",
            False,
            ago(30),
        )
        assert older.body.startswith("Escalating to L2")
        assert newest.truncated
        assert LONG_NOTE.startswith(newest.body)
        assert len(newest.body) <= limits.note.chars
        assert len(newest.body.encode()) <= limits.note.bytes

    async def test_the_number_of_notes_can_be_chosen_up_to_the_limit(
        self, service: TicketService, limits: Limits
    ) -> None:
        assert len((await service.get(ARJUN, ESCALATED_BY_PRIYA)).notes) == 3
        assert (await service.get(ARJUN, ESCALATED_BY_PRIYA, notes=0)).notes == ()
        for notes in (-1, limits.notes.max + 1):
            with pytest.raises(ValidationFailedError, match="notes"):
                await service.get(ARJUN, ESCALATED_BY_PRIYA, notes=notes)

    async def test_a_refund_waiting_for_a_supervisor_is_described_from_the_request(
        self, service: TicketService
    ) -> None:
        view = await service.get(KAVITA, AWAITING_SUPERVISOR)

        assert view.needs_my_approval
        assert view.refund is not None
        assert view.refund.model_dump() == {
            "state": "waiting_for_supervisor",
            "amount_minor": 890000,
            "currency": "INR",
            "reason_code": "lost_in_transit",
            "requested_by_name": "Arjun Mehta",
            "approved_by_name": None,
            "failure_reason": None,
            "refund_id": None,
        }

    async def test_a_supervisor_is_never_asked_to_approve_their_own_request(
        self, service: TicketService
    ) -> None:
        view = await service.get(KAVITA, REQUESTED_BY_KAVITA)

        assert view.refund is not None
        assert (view.refund.state, view.refund.requested_by_name) == (
            "waiting_for_supervisor",
            "Kavita Rao",
        )
        assert not view.needs_my_approval

    async def test_with_two_requests_each_person_is_shown_the_one_that_is_not_theirs(
        self, service: TicketService
    ) -> None:
        as_requester = await service.get(ARJUN, AWAITING_SUPERVISOR)

        assert as_requester.refund is not None
        assert as_requester.refund.requested_by_name == "Kavita Rao"
        assert not as_requester.needs_my_approval

    async def test_an_approved_request_past_its_expiry_no_longer_hides_the_refund(
        self, service: TicketService
    ) -> None:
        view = await service.get(ARJUN, PAID)

        assert view.refund is not None
        assert (view.refund.state, view.refund.refund_id) == ("paid", 900305)

    async def test_an_approved_request_names_the_supervisor_and_needs_no_approval(
        self, service: TicketService
    ) -> None:
        view = await service.get(KAVITA, APPROVED_BY_SUPERVISOR)

        assert not view.needs_my_approval
        assert view.refund is not None
        assert (view.refund.state, view.refund.approved_by_name) == (
            "approved_by_supervisor",
            "Kavita Rao",
        )

    async def test_a_failed_refund_gives_its_number_and_reason(
        self, service: TicketService
    ) -> None:
        view = await service.get(ARJUN, PAYMENT_FAILED)

        assert view.refund is not None
        assert view.refund.model_dump() == {
            "state": "failed",
            "amount_minor": 20000,
            "currency": "INR",
            "reason_code": "goodwill_delay",
            "requested_by_name": "Arjun Mehta",
            "approved_by_name": "Arjun Mehta",
            "failure_reason": "Card closed",
            "refund_id": 900304,
        }

    @pytest.mark.parametrize("ticket_id", [REQUEST_EXPIRED, SELF_APPROVAL_PENDING, PLAIN])
    async def test_expired_self_approved_and_rejected_requests_are_not_refunds(
        self, service: TicketService, ticket_id: int
    ) -> None:
        view = await service.get(KAVITA, ticket_id)

        assert view.refund is None
        assert not view.needs_my_approval

    async def test_another_region_and_no_such_ticket_look_the_same(
        self, service: TicketService
    ) -> None:
        with pytest.raises(NotFoundError) as missing:
            await service.get(PRIYA, NO_TICKET)
        with pytest.raises(NotFoundError) as elsewhere:
            await service.get(PRIYA, SOUTH_PLAIN)

        assert missing.value.body() == elsewhere.value.body()

    async def test_a_supervisor_reads_tickets_in_every_region(self, service: TicketService) -> None:
        assert (await service.get(KAVITA, SOUTH_PLAIN)).notes[0].author_name == "Bala Nair"

    async def test_an_administrator_cannot_read_or_list_tickets(
        self, service: TicketService
    ) -> None:
        with pytest.raises(PermissionDeniedError):
            await service.get(SAM, PLAIN)
        with pytest.raises(PermissionDeniedError):
            await service.list(SAM, TicketQuery(view="all_open"))

"""How reading an order must behave, whichever backend holds the rows."""

import pytest

from opscp_common.errors import NotFoundError, PermissionDeniedError, ValidationFailedError
from opscp_common.limits import Limits
from opscp_mcp.services.orders import OrderService
from opscp_mcp_testing.cases.backend import Backend
from opscp_mcp_testing.identities import KAVITA, PRIYA, ROHAN, SAM
from opscp_mcp_testing.world import (
    ASHA,
    DELIVERED,
    MOVING,
    NEVER_SCANNED,
    NO_ORDER,
    NOW,
    PAYMENT_HELD,
    SCANNER_LOOP,
    SOUTH_ORDER,
    STUCK_ON_ADDRESS,
    ago,
)


class OrderCases:
    @pytest.fixture
    def service(self, backend: Backend, limits: Limits) -> OrderService:
        return OrderService(backend.orders, limits, lambda: NOW)

    async def test_an_order_is_returned_with_its_age_refunds_and_events(
        self, service: OrderService
    ) -> None:
        view = await service.get(PRIYA, STUCK_ON_ADDRESS)

        assert view.model_dump(exclude={"events", "placed_at"}) == {
            "order_id": STUCK_ON_ADDRESS,
            "customer_id": ASHA,
            "status": "packed",
            "status_age_hours": 96,
            "is_stuck": True,
            "amount_minor": 620000,
            "currency": "INR",
            "refunded_minor": 10000,
            "carrier": None,
            "tracking_ref": None,
        }
        assert view.events is not None
        assert view.events.total == 6
        assert view.events.older_by_type == {}
        assert [event.occurred_at for event in view.events.recent] == [
            ago(hours) for hours in (23, 47, 71, 95, 96.5, 120)
        ]
        assert (
            view.events.recent[0].summary == "Address validation failed (reason: postcode_mismatch)"
        )
        assert view.events.recent[-1].summary == "Payment released"

    async def test_only_the_most_recent_events_are_listed_and_the_rest_are_counted(
        self, service: OrderService, limits: Limits
    ) -> None:
        view = await service.get(PRIYA, SCANNER_LOOP)

        assert view.events is not None
        assert len(view.events.recent) == limits.events.recent
        assert {event.type for event in view.events.recent} == {"carrier_scan"}
        assert view.events.total == 13
        assert view.events.older_by_type == {"carrier_scan": 2, "carrier_pickup": 1}

    async def test_events_can_be_left_out(self, service: OrderService) -> None:
        view = await service.get(PRIYA, SCANNER_LOOP, events="none")

        assert view.events is None
        assert view.is_stuck

    @pytest.mark.parametrize(
        ("order_id", "stuck"),
        [
            pytest.param(PAYMENT_HELD, True, id="paid for 30 hours"),
            pytest.param(SCANNER_LOOP, True, id="a scanner repeating itself is not movement"),
            pytest.param(MOVING, False, id="a new scan 10 hours ago"),
            pytest.param(NEVER_SCANNED, True, id="no scan since pickup 80 hours ago"),
            pytest.param(DELIVERED, False, id="delivered"),
        ],
    )
    async def test_an_order_is_stuck_when_it_has_not_moved_for_too_long(
        self, service: OrderService, order_id: int, stuck: bool
    ) -> None:
        assert (await service.get(PRIYA, order_id, events="none")).is_stuck is stuck

    async def test_an_order_in_another_region_is_not_found(self, service: OrderService) -> None:
        with pytest.raises(NotFoundError):
            await service.get(PRIYA, SOUTH_ORDER)
        with pytest.raises(NotFoundError):
            await service.get(ROHAN, STUCK_ON_ADDRESS)

    async def test_a_supervisor_reads_orders_in_every_region(self, service: OrderService) -> None:
        assert (await service.get(KAVITA, SOUTH_ORDER)).status_age_hours == 60
        assert (await service.get(KAVITA, STUCK_ON_ADDRESS)).status_age_hours == 96

    async def test_another_region_and_no_such_order_look_the_same(
        self, service: OrderService
    ) -> None:
        with pytest.raises(NotFoundError) as missing:
            await service.get(PRIYA, NO_ORDER)
        with pytest.raises(NotFoundError) as elsewhere:
            await service.get(PRIYA, SOUTH_ORDER)

        assert missing.value.body() == elsewhere.value.body()

    @pytest.mark.parametrize("order_id", [0, -5, 2**63, 9400111899223344556677])
    async def test_a_number_that_cannot_be_an_order_is_refused_as_invalid(
        self, service: OrderService, order_id: int
    ) -> None:
        with pytest.raises(ValidationFailedError):
            await service.get(PRIYA, order_id)

    async def test_an_administrator_cannot_read_orders(self, service: OrderService) -> None:
        with pytest.raises(PermissionDeniedError):
            await service.get(SAM, STUCK_ON_ADDRESS)

"""How customer lookup must behave, whichever backend holds the rows."""

import pytest

from opscp_common.errors import NotFoundError, PermissionDeniedError, ValidationFailedError
from opscp_mcp.services.customers import CustomerService
from opscp_mcp_testing.cases.backend import Backend
from opscp_mcp_testing.identities import ARJUN, KAVITA, PRIYA, ROHAN, SAM
from opscp_mcp_testing.world import ASHA, BALA, NOBODY, WORLD

ASHA_ROW, BALA_ROW = WORLD.customers[:2]


class CustomerCases:
    @pytest.fixture
    def service(self, backend: Backend) -> CustomerService:
        return CustomerService(backend.customers)

    async def test_an_engineer_without_pii_access_sees_masked_contact_details(
        self, service: CustomerService
    ) -> None:
        view = await service.lookup(PRIYA, customer_id=ASHA)

        assert view.model_dump() == {
            "customer_id": ASHA,
            "display_name": "Asha Rao",
            "tier": "premium",
            "region": "west",
            "email": "a***@example.in",
            "phone": "+** ***** ***01",
            "contact_masked": True,
            "open_ticket_count": 2,
            "created_at": ASHA_ROW.created_at,
        }

    async def test_an_engineer_with_pii_access_sees_them_in_full(
        self, service: CustomerService
    ) -> None:
        view = await service.lookup(ARJUN, customer_id=ASHA)

        assert (view.email, view.phone, view.contact_masked) == (
            "asha.rao@example.in",
            "+91 58200 00001",
            False,
        )

    async def test_a_customer_is_found_by_email_whatever_the_case(
        self, service: CustomerService
    ) -> None:
        view = await service.lookup(PRIYA, email="Asha.Rao@Example.IN")

        assert (view.customer_id, view.email, view.contact_masked) == (
            ASHA,
            "a***@example.in",
            True,
        )

    async def test_a_missing_phone_number_stays_missing(self, service: CustomerService) -> None:
        assert (await service.lookup(ROHAN, customer_id=BALA)).phone is None
        assert (await service.lookup(KAVITA, customer_id=BALA)).phone is None

    @pytest.mark.parametrize("lookup", [{"customer_id": BALA}, {"email": BALA_ROW.email}])
    async def test_a_customer_in_another_region_is_not_found(
        self, service: CustomerService, lookup: dict[str, int | str]
    ) -> None:
        with pytest.raises(NotFoundError):
            await service.lookup(PRIYA, **lookup)  # pyright: ignore[reportArgumentType] -- one of the two keyword arguments

    async def test_a_supervisor_finds_customers_in_every_region(
        self, service: CustomerService
    ) -> None:
        assert (await service.lookup(KAVITA, customer_id=ASHA)).region == "west"
        assert (await service.lookup(KAVITA, customer_id=BALA)).region == "south"

    async def test_another_region_and_no_such_customer_look_the_same(
        self, service: CustomerService
    ) -> None:
        with pytest.raises(NotFoundError) as missing:
            await service.lookup(PRIYA, customer_id=NOBODY)
        with pytest.raises(NotFoundError) as elsewhere:
            await service.lookup(PRIYA, customer_id=BALA)

        assert missing.value.body() == elsewhere.value.body()

    @pytest.mark.parametrize(
        "lookup",
        [
            {"customer_id": 0},
            {"customer_id": -1},
            {"customer_id": 2**63},
            {"email": ""},
            {"email": "no-at-sign"},
            {"email": "nul\x00@example.in"},
            {"email": "a" * 250 + "@example.in"},
        ],
    )
    async def test_a_value_the_database_could_not_hold_is_refused_as_invalid(
        self, service: CustomerService, lookup: dict[str, int | str]
    ) -> None:
        with pytest.raises(ValidationFailedError):
            await service.lookup(PRIYA, **lookup)  # pyright: ignore[reportArgumentType] -- one of the two keyword arguments

    @pytest.mark.parametrize("lookup", [{}, {"customer_id": ASHA, "email": ASHA_ROW.email}])
    async def test_exactly_one_way_to_identify_the_customer_is_required(
        self, service: CustomerService, lookup: dict[str, int | str]
    ) -> None:
        with pytest.raises(ValidationFailedError):
            await service.lookup(PRIYA, **lookup)  # pyright: ignore[reportArgumentType] -- zero or two keyword arguments

    async def test_an_administrator_cannot_look_up_customers(
        self, service: CustomerService
    ) -> None:
        with pytest.raises(PermissionDeniedError):
            await service.lookup(SAM, customer_id=ASHA)

    async def test_an_engineer_without_a_region_is_refused(self, service: CustomerService) -> None:
        with pytest.raises(PermissionDeniedError, match="no region"):
            await service.lookup(PRIYA.model_copy(update={"region": None}), customer_id=ASHA)

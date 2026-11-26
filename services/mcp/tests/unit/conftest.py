import pytest

from opscp_mcp_testing.cases.backend import Backend
from opscp_mcp_testing.fakes.meridian import FakeCustomers, FakeOrders
from opscp_mcp_testing.world import WORLD


@pytest.fixture
def backend() -> Backend:
    return Backend(customers=FakeCustomers(WORLD), orders=FakeOrders(WORLD))

import pytest

from opscp_common.errors import InternalError
from opscp_common.limits import Limits
from opscp_mcp.ports.knowledge import Policies
from opscp_mcp.services.policies import PolicyService
from opscp_mcp_testing.cases.policies import PolicyCases
from opscp_mcp_testing.fakes.knowledge import FakePolicies
from opscp_mcp_testing.identities import PRIYA
from opscp_mcp_testing.paths import POLICIES_DIR

SHIPPING = (POLICIES_DIR / "shipping-sla.md").read_text(encoding="utf-8")
LONG_SECTION = """---
name: refund-policy
version: 3
effective_date: 2026-10-01
---

## Scope {#scope}

A summary.

""" + ("word " * 1000)


class TestPoliciesInMemory(PolicyCases):
    @pytest.fixture
    def policies(self) -> Policies:
        return FakePolicies.from_directory(POLICIES_DIR)


async def test_a_section_longer_than_the_limit_is_cut_and_flagged(limits: Limits) -> None:
    service = PolicyService(FakePolicies({"refund-policy": LONG_SECTION}), limits)

    view = await service.get(PRIYA, "refund-policy", section="scope")

    assert view.section is not None
    assert len(view.section.content) == limits.policy.section_chars
    assert view.section.truncated


@pytest.mark.parametrize(
    "stored",
    [
        pytest.param({}, id="missing"),
        pytest.param({"refund-policy": "not a policy document"}, id="unreadable"),
        pytest.param({"refund-policy": SHIPPING}, id="another document under this name"),
    ],
)
async def test_a_store_that_does_not_hold_the_document_is_a_server_fault(
    limits: Limits, stored: dict[str, str]
) -> None:
    service = PolicyService(FakePolicies(stored), limits)

    with pytest.raises(InternalError) as fault:
        await service.get(PRIYA, "refund-policy")

    assert fault.value.body().details == {}
    assert "refund" not in fault.value.body().message

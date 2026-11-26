"""How reading a policy must behave, whichever store holds the documents."""

from datetime import date

import pytest

from opscp_common.errors import NotFoundError, PermissionDeniedError, ValidationFailedError
from opscp_common.limits import Limits
from opscp_mcp.ports.knowledge import Policies
from opscp_mcp.services.policies import POLICY_NAMES, PolicyService
from opscp_mcp_testing.identities import KAVITA, PRIYA, SAM

SECTIONS = {
    "refund-policy": [
        "scope",
        "eligibility",
        "goodwill",
        "full-refund",
        "approval-limits",
        "exclusions",
    ],
    "shipping-sla": ["carrier-slas", "stuck-definitions", "escalation"],
    "premium-customer-handling": ["priority", "tone", "expedited-shipping"],
}


class PolicyCases:
    @pytest.fixture
    def service(self, policies: Policies, limits: Limits) -> PolicyService:
        return PolicyService(policies, limits)

    def test_the_cases_cover_every_policy_document(self) -> None:
        assert set(SECTIONS) == set(POLICY_NAMES)

    @pytest.mark.parametrize("name", POLICY_NAMES)
    async def test_without_a_section_only_the_outline_is_returned(
        self, service: PolicyService, name: str
    ) -> None:
        view = await service.get(PRIYA, name)

        assert view.name == name
        assert view.section is None
        assert view.sections is not None
        assert [section.id for section in view.sections] == SECTIONS[name]
        assert all(section.title and section.summary for section in view.sections)

    async def test_the_outline_carries_the_version_and_effective_date(
        self, service: PolicyService
    ) -> None:
        view = await service.get(PRIYA, "refund-policy")

        assert (view.version, view.effective_date) == (3, date(2026, 10, 1))

    async def test_one_section_is_returned_with_its_text(self, service: PolicyService) -> None:
        view = await service.get(KAVITA, "refund-policy", section="goodwill")

        assert view.sections is None
        assert view.section is not None
        assert (view.section.id, view.section.title) == ("goodwill", "Goodwill refunds")
        assert "| Premium | 72 hours | 15% of order value |" in view.section.content
        assert not view.section.truncated
        assert "## " not in view.section.content

    @pytest.mark.parametrize("name", POLICY_NAMES)
    async def test_every_section_can_be_read_and_fits_the_limit(
        self, service: PolicyService, limits: Limits, name: str
    ) -> None:
        for section_id in SECTIONS[name]:
            view = await service.get(PRIYA, name, section=section_id)

            assert view.section is not None
            assert 0 < len(view.section.content) <= limits.policy.section_chars
            assert not view.section.truncated

    async def test_an_unknown_section_is_not_found_and_the_real_ones_are_listed(
        self, service: PolicyService
    ) -> None:
        with pytest.raises(NotFoundError) as refusal:
            await service.get(PRIYA, "shipping-sla", section="refunds")

        assert refusal.value.body().details == {"sections": SECTIONS["shipping-sla"]}

    @pytest.mark.parametrize(
        "name", ["refund-policy.md", "../refund-policy", "REFUND-POLICY", "", "pricing"]
    )
    async def test_a_name_that_is_not_one_of_the_documents_is_refused(
        self, service: PolicyService, name: str
    ) -> None:
        with pytest.raises(ValidationFailedError) as refusal:
            await service.get(PRIYA, name)

        assert refusal.value.body().details == {"names": list(POLICY_NAMES)}

    async def test_an_administrator_cannot_read_policies(self, service: PolicyService) -> None:
        with pytest.raises(PermissionDeniedError):
            await service.get(SAM, "refund-policy")

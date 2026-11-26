import hashlib
import re
from typing import get_args

import pytest

from opscp_common.documents import load_policies
from opscp_common.errors import InternalError, PermissionDeniedError
from opscp_common.limits import Limits
from opscp_common.vocabulary import OrderStatus
from opscp_mcp.adapters.memory_runbooks import InMemoryRunbooks
from opscp_mcp.services.resources import (
    ORDER_LIFECYCLE_URI,
    ORDER_MOVES,
    RUNBOOK_INDEX_URI,
    ReferenceService,
)
from opscp_mcp_testing.identities import PRIYA, SAM
from opscp_mcp_testing.paths import POLICIES_DIR, RUNBOOKS_DIR

CHARS_PER_TOKEN = 4
LIFECYCLE_TOKENS = 250
REFUND_SUMMARY_TOKENS = 300
FINAL_STATUSES = {"delivered", "cancelled", "lost"}


@pytest.fixture(scope="module")
def runbooks() -> InMemoryRunbooks:
    return InMemoryRunbooks.from_directory(RUNBOOKS_DIR)


@pytest.fixture
def service(runbooks: InMemoryRunbooks, limits: Limits) -> ReferenceService:
    return ReferenceService(runbooks, limits)


def test_every_order_status_has_its_moves_and_only_final_ones_have_none() -> None:
    statuses = set(get_args(OrderStatus.__value__))

    assert set(ORDER_MOVES) == statuses
    assert {status for status, following in ORDER_MOVES.items() if not following} == FINAL_STATUSES
    assert all(set(following) <= statuses for following in ORDER_MOVES.values())


def test_the_order_lifecycle_states_the_moves_and_the_stuck_limits(
    service: ReferenceService, limits: Limits
) -> None:
    reference = service.order_lifecycle(PRIYA)

    assert reference.uri == ORDER_LIFECYCLE_URI
    assert "- packed -> shipped, cancelled" in reference.text
    assert "- delivered is final" in reference.text
    assert f"paid for over {limits.stuck.paid_hours} hours" in reference.text
    assert f"packed for over {limits.stuck.packed_hours} hours" in reference.text
    assert f"scan for over {limits.stuck.shipped_no_scan_hours} hours" in reference.text
    assert len(reference.text) <= LIFECYCLE_TOKENS * CHARS_PER_TOKEN


def test_the_refund_summary_states_the_limits_the_code_enforces(
    service: ReferenceService, limits: Limits
) -> None:
    text = service.refund_summary(PRIYA).text
    premium, standard = limits.goodwill.premium, limits.goodwill.standard

    assert f"delay of {premium.min_delay_hours} hours or more, up to {premium.max_percent}%" in text
    assert f"{standard.min_delay_hours} hours or more, up to {standard.max_percent}%" in text
    assert "Up to INR 5,000 the requesting L2 engineer may approve" in text
    assert limits.approval.self_limit_minor == 500000
    assert len(text) <= REFUND_SUMMARY_TOKENS * CHARS_PER_TOKEN


def test_the_refund_summary_points_at_every_section_of_the_refund_policy(
    service: ReferenceService,
) -> None:
    (policy,) = [p for p in load_policies(POLICIES_DIR) if p.name == "refund-policy"]

    cited = re.findall(r"^- \[([a-z-]+)\]", service.refund_summary(PRIYA).text, re.MULTILINE)

    assert cited == [section.id for section in policy.sections]


async def test_the_runbook_index_has_one_line_per_standard_runbook(
    service: ReferenceService,
) -> None:
    reference = await service.runbook_index(PRIYA)

    lines = reference.text.splitlines()
    assert reference.uri == RUNBOOK_INDEX_URI
    assert len(lines) == 30
    assert lines[0].startswith("rb-001 | Address validation failed | Confirm the address")
    assert "rb-901" not in reference.text
    assert "SYSTEM:" not in reference.text


async def test_an_index_longer_than_the_limit_is_a_server_fault(
    runbooks: InMemoryRunbooks, limits: Limits
) -> None:
    short = limits.model_copy(
        update={"runbooks": limits.runbooks.model_copy(update={"index_max": 29})}
    )

    with pytest.raises(InternalError):
        await ReferenceService(runbooks, short).runbook_index(PRIYA)


async def test_a_version_is_the_start_of_the_hash_of_the_text(service: ReferenceService) -> None:
    references = [
        service.order_lifecycle(PRIYA),
        service.refund_summary(PRIYA),
        await service.runbook_index(PRIYA),
    ]

    for reference in references:
        assert reference.version == hashlib.sha256(reference.text.encode()).hexdigest()[:12]
    assert len({reference.version for reference in references}) == len(references)


def test_the_version_changes_when_a_limit_changes(
    runbooks: InMemoryRunbooks, limits: Limits
) -> None:
    changed = limits.model_copy(
        update={"stuck": limits.stuck.model_copy(update={"packed_hours": 49})}
    )

    before = ReferenceService(runbooks, limits).order_lifecycle(PRIYA)
    after = ReferenceService(runbooks, changed).order_lifecycle(PRIYA)

    assert before.version != after.version
    assert "packed for over 49 hours" in after.text


async def test_an_administrator_cannot_read_the_references(service: ReferenceService) -> None:
    with pytest.raises(PermissionDeniedError):
        service.order_lifecycle(SAM)
    with pytest.raises(PermissionDeniedError):
        service.refund_summary(SAM)
    with pytest.raises(PermissionDeniedError):
        await service.runbook_index(SAM)

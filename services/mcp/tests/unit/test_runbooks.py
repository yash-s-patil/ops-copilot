from datetime import date

import pytest

from opscp_common.documents import RUNBOOK_SECTIONS, Runbook, Section
from opscp_common.errors import PermissionDeniedError, ValidationFailedError
from opscp_common.limits import Limits
from opscp_mcp.adapters.memory_runbooks import InMemoryRunbooks
from opscp_mcp.ports.knowledge import CHAOS, STANDARD, Collection
from opscp_mcp.services.runbooks import RunbookService
from opscp_mcp_testing.identities import ARJUN, PRIYA, SAM
from opscp_mcp_testing.paths import RUNBOOKS_DIR

ONLY_STANDARD: frozenset[Collection] = frozenset({STANDARD})
WITH_CHAOS: frozenset[Collection] = frozenset({STANDARD, CHAOS})
POISONED = "rb-901"
REFERENCE_QUERIES = [
    ("address validation failed, order stuck in packed", "rb-001", "rb-008"),
    ("carrier exception, shipment not moving", "rb-012", None),
    ("payment hold, order not released", "rb-004", None),
    ("shipment lost in transit, refund", "rb-013", "rb-022"),
    ("order stuck in packed, no pickup", "rb-008", None),
    ("tracking not updating, repeated scans", "rb-016", None),
]


@pytest.fixture(scope="module")
def runbooks() -> InMemoryRunbooks:
    return InMemoryRunbooks.from_directory(RUNBOOKS_DIR)


@pytest.fixture
def service(runbooks: InMemoryRunbooks, limits: Limits) -> RunbookService:
    return RunbookService(runbooks, limits)


@pytest.mark.parametrize(("query", "first", "also"), REFERENCE_QUERIES)
async def test_a_reference_query_finds_its_runbook_first(
    service: RunbookService, query: str, first: str, also: str | None
) -> None:
    found = await service.search(PRIYA, query)

    ids = [hit.runbook_id for hit in found.results]
    assert ids[0] == first
    assert also is None or also in ids


async def test_results_are_distinct_runbooks_best_first_with_their_section(
    service: RunbookService, limits: Limits
) -> None:
    found = await service.search(PRIYA, "carrier exception, shipment not moving")

    assert len(found.results) == limits.runbooks.top_k_default
    assert len({hit.runbook_id for hit in found.results}) == len(found.results)
    assert [hit.score for hit in found.results] == sorted(
        (hit.score for hit in found.results), reverse=True
    )
    best = found.results[0]
    assert (best.title, best.section) == ("Carrier exception handling", "symptoms")
    assert "`carrier_exception`" in best.content
    assert (best.truncated, best.quarantined, found.degraded) == (False, False, False)


async def test_the_number_of_results_can_be_chosen_up_to_the_limit(
    service: RunbookService, limits: Limits
) -> None:
    most = await service.search(PRIYA, "refund", top_k=limits.runbooks.top_k_max)

    assert len(most.results) == limits.runbooks.top_k_max
    assert len((await service.search(PRIYA, "refund", top_k=1)).results) == 1
    for top_k in (0, -1, limits.runbooks.top_k_max + 1):
        with pytest.raises(ValidationFailedError, match="top_k"):
            await service.search(PRIYA, "refund", top_k=top_k)


@pytest.mark.parametrize("query", ["", "  ", "ab", " a \n  ", "x" * 201, "refund\x00now"])
async def test_a_query_outside_the_limits_is_refused(service: RunbookService, query: str) -> None:
    with pytest.raises(ValidationFailedError, match="query"):
        await service.search(PRIYA, query)


async def test_a_query_that_matches_nothing_returns_no_results(service: RunbookService) -> None:
    assert (await service.search(PRIYA, "zzzz qqqq")).results == ()
    assert (await service.search(PRIYA, "the and of")).results == ()


async def test_every_support_role_may_search_and_an_administrator_may_not(
    service: RunbookService,
) -> None:
    assert (await service.search(ARJUN, "payment hold")).results
    with pytest.raises(PermissionDeniedError):
        await service.search(SAM, "payment hold")


async def test_the_service_never_returns_the_poisoned_runbook(
    service: RunbookService, limits: Limits
) -> None:
    for query in ("carrier exception, shipment not moving", "legacy fast-track", "full refund"):
        found = await service.search(PRIYA, query, top_k=limits.runbooks.top_k_max)

        assert POISONED not in [hit.runbook_id for hit in found.results]
        assert not any("SYSTEM:" in hit.content for hit in found.results)


async def test_the_poisoned_runbook_would_rank_in_the_top_three_if_it_were_searched(
    runbooks: InMemoryRunbooks,
) -> None:
    found = await runbooks.search(
        "carrier exception, shipment not moving", limit=3, collections=WITH_CHAOS
    )

    assert POISONED in [chunk.runbook_id for chunk in found]


async def test_the_catalog_lists_the_thirty_standard_runbooks_in_order(
    runbooks: InMemoryRunbooks,
) -> None:
    catalog = await runbooks.catalog()

    assert [entry.runbook_id for entry in catalog] == [f"rb-{n:03d}" for n in range(1, 31)]
    assert catalog[0].title == "Address validation failed"
    assert all(entry.summary for entry in catalog)


async def test_a_long_section_is_cut_to_the_chunk_limit(limits: Limits) -> None:
    long_runbook = Runbook(
        id="rb-001",
        title="Long",
        summary="A summary",
        version=1,
        updated=date(2026, 9, 22),
        sections=tuple(
            Section(id=name, title=name.title(), content="refund " * 1000)
            for name in RUNBOOK_SECTIONS
        ),
    )
    index = InMemoryRunbooks([long_runbook])

    found = await RunbookService(index, limits).search(PRIYA, "refund")

    (hit,) = found.results
    assert len(hit.content) == limits.runbook.chunk_chars
    assert hit.truncated

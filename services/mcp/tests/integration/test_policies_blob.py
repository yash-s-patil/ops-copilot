from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from azure.storage.blob.aio import ContainerClient

from opscp_common.errors import InternalError
from opscp_common.limits import Limits
from opscp_mcp.adapters.blob import BlobPolicies
from opscp_mcp.ports.knowledge import Policies
from opscp_mcp_testing.cases.policies import PolicyCases
from opscp_mcp_testing.paths import POLICIES_DIR

from .storage import NOT_TEXT, scratch_policies

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def container() -> AsyncGenerator[ContainerClient]:
    async with scratch_policies() as client:
        yield client


@pytest.fixture
def policies(container: ContainerClient, limits: Limits) -> Policies:
    return BlobPolicies(
        container, timeout_s=limits.timeout.blob_s, max_bytes=limits.policy.document_bytes
    )


class TestPoliciesInBlob(PolicyCases):
    pass


async def test_a_stored_document_is_read_as_text(policies: Policies) -> None:
    text = await policies.read("refund-policy")

    assert text == (POLICIES_DIR / "refund-policy.md").read_text(encoding="utf-8")


async def test_a_document_that_is_not_stored_reads_as_none(policies: Policies) -> None:
    assert await policies.read("pricing") is None


async def test_a_stored_blob_that_is_not_text_is_a_server_fault(policies: Policies) -> None:
    with pytest.raises(InternalError):
        await policies.read(NOT_TEXT)


async def test_a_stored_document_over_the_size_limit_is_refused(
    container: ContainerClient, limits: Limits
) -> None:
    strict = BlobPolicies(container, timeout_s=limits.timeout.blob_s, max_bytes=10)

    with pytest.raises(InternalError):
        await strict.read("refund-policy")

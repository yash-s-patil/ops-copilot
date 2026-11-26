from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest
import pytest_asyncio
from azure.storage.blob.aio import ContainerClient

from opscp_common.blob import policies_container
from opscp_common.errors import InternalError
from opscp_common.limits import Limits
from opscp_common.settings import LOCAL, StorageSettings
from opscp_mcp.adapters.blob import POLICY_EXTENSION, BlobPolicies
from opscp_mcp.ports.knowledge import Policies
from opscp_mcp_testing.cases.policies import PolicyCases
from opscp_mcp_testing.paths import POLICIES_DIR, REPO_ROOT

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def container() -> AsyncGenerator[ContainerClient]:
    local = StorageSettings(_env_file=REPO_ROOT / ".env")  # pyright: ignore[reportCallIssue] -- fields come from the env file
    assert local.environment == LOCAL
    scratch = local.model_copy(update={"policies_container": f"mcp-test-{uuid4().hex[:12]}"})
    async with policies_container(scratch) as client:
        await client.create_container()
        for path in POLICIES_DIR.glob(f"*{POLICY_EXTENSION}"):
            await client.upload_blob(path.name, path.read_bytes())
        await client.upload_blob("not-text.md", b"\xff\xfe\x00binary")
        yield client
        await client.delete_container()


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
        await policies.read("not-text")


async def test_a_stored_document_over_the_size_limit_is_refused(
    container: ContainerClient, limits: Limits
) -> None:
    strict = BlobPolicies(container, timeout_s=limits.timeout.blob_s, max_bytes=10)

    with pytest.raises(InternalError):
        await strict.read("refund-policy")

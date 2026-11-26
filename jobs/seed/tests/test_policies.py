from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest
import pytest_asyncio
from azure.storage.blob.aio import ContainerClient

from opscp_common.blob import policies_container
from opscp_common.documents import parse_policy
from opscp_common.settings import LOCAL, StorageSettings
from opscp_seed.policies import read_policies, upload_policies

from .conftest import REPO_ROOT

pytestmark = pytest.mark.integration

POLICIES_DIR = REPO_ROOT / "data" / "policies"
NAMES = ["premium-customer-handling", "refund-policy", "shipping-sla"]


@pytest_asyncio.fixture
async def container() -> AsyncGenerator[ContainerClient]:
    local = StorageSettings(_env_file=REPO_ROOT / ".env")  # pyright: ignore[reportCallIssue] -- fields come from the env file
    assert local.environment == LOCAL
    scratch = local.model_copy(update={"policies_container": f"seed-test-{uuid4().hex[:12]}"})
    async with policies_container(scratch) as client:
        yield client
        await client.delete_container()


async def _stored(container: ContainerClient) -> dict[str, str]:
    stored: dict[str, str] = {}
    async for blob in container.list_blobs():
        download = await container.download_blob(blob.name, encoding="utf-8")
        stored[blob.name] = await download.readall()
    return stored


async def test_every_policy_is_stored_under_its_name_exactly_as_it_was_checked(
    container: ContainerClient,
) -> None:
    texts = read_policies(POLICIES_DIR)

    await upload_policies(container, texts)

    stored = await _stored(container)
    assert stored == {f"{name}.md": texts[name] for name in NAMES}
    assert all(parse_policy(stored[f"{name}.md"]).name == name for name in NAMES)


async def test_a_policy_is_stored_as_markdown(container: ContainerClient) -> None:
    await upload_policies(container, read_policies(POLICIES_DIR))

    properties = await container.get_blob_client("refund-policy.md").get_blob_properties()

    assert properties.content_settings.content_type == "text/markdown; charset=utf-8"


async def test_uploading_again_replaces_what_is_stored(container: ContainerClient) -> None:
    texts = read_policies(POLICIES_DIR)
    await upload_policies(container, texts)
    revised = texts | {"shipping-sla": texts["shipping-sla"].replace("version: 2", "version: 3")}

    await upload_policies(container, revised)

    stored = await _stored(container)
    assert len(stored) == len(NAMES)
    assert parse_policy(stored["shipping-sla.md"]).version == 3


async def test_text_outside_ascii_is_stored_as_utf8(container: ContainerClient) -> None:
    await upload_policies(container, {"note": "नीति — ₹5,000"})

    assert await _stored(container) == {"note.md": "नीति — ₹5,000"}

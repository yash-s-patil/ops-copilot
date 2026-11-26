"""A scratch container in the local storage emulator, holding the policy documents."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import uuid4

from azure.storage.blob.aio import ContainerClient

from opscp_common.blob import policies_container
from opscp_common.settings import LOCAL, StorageSettings
from opscp_mcp.adapters.blob import POLICY_EXTENSION
from opscp_mcp_testing.paths import POLICIES_DIR, REPO_ROOT

NOT_TEXT = "not-text"


@asynccontextmanager
async def scratch_policies() -> AsyncGenerator[ContainerClient]:
    """Created for one test module and deleted after it. Refuses anything but the emulator."""
    local = StorageSettings(_env_file=REPO_ROOT / ".env")  # pyright: ignore[reportCallIssue] -- fields come from the env file
    if local.environment != LOCAL:
        raise RuntimeError("the storage tests run against the local emulator only")
    scratch = local.model_copy(update={"policies_container": f"mcp-test-{uuid4().hex[:12]}"})
    async with policies_container(scratch) as client:
        await client.create_container()
        try:
            for path in POLICIES_DIR.glob(f"*{POLICY_EXTENSION}"):
                await client.upload_blob(path.name, path.read_bytes())
            await client.upload_blob(f"{NOT_TEXT}{POLICY_EXTENSION}", b"\xff\xfe\x00binary")
            yield client
        finally:
            await client.delete_container()

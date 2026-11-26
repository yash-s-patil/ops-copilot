"""The Blob container that holds the policy documents."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from azure.identity.aio import ManagedIdentityCredential
from azure.storage.blob.aio import ContainerClient

from opscp_common.settings import StorageSettings


@asynccontextmanager
async def policies_container(
    settings: StorageSettings, *, sdk_retries: int | None = None
) -> AsyncGenerator[ContainerClient]:
    """Opened with the managed identity, or with the emulator's connection string locally.

    A caller that retries for itself sets `sdk_retries` to zero; otherwise the client's own
    number of retries applies.
    """
    # The client takes its pipeline options as untyped keywords.
    retrying: dict[str, Any] = {} if sdk_retries is None else {"retry_total": sdk_retries}
    if settings.blob_connection_string is not None:
        async with ContainerClient.from_connection_string(
            settings.blob_connection_string.get_secret_value(),
            container_name=settings.policies_container,
            **retrying,
        ) as container:
            yield container
        return
    if settings.blob_account_url is None or settings.azure_client_id is None:
        raise ValueError("StorageSettings names neither an emulator nor an account and identity")
    async with (
        ManagedIdentityCredential(client_id=str(settings.azure_client_id)) as credential,
        ContainerClient(
            settings.blob_account_url,
            settings.policies_container,
            credential=credential,
            **retrying,
        ) as container,
    ):
        yield container

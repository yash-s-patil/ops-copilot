"""The Blob container that holds the policy documents."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from azure.identity.aio import ManagedIdentityCredential
from azure.storage.blob.aio import ContainerClient

from opscp_common.settings import StorageSettings


@asynccontextmanager
async def policies_container(settings: StorageSettings) -> AsyncGenerator[ContainerClient]:
    """Opened with the managed identity, or with the emulator's connection string locally."""
    if settings.blob_connection_string is not None:
        async with ContainerClient.from_connection_string(
            settings.blob_connection_string.get_secret_value(),
            container_name=settings.policies_container,
        ) as container:
            yield container
        return
    if settings.blob_account_url is None or settings.azure_client_id is None:
        raise ValueError("StorageSettings names neither an emulator nor an account and identity")
    async with (
        ManagedIdentityCredential(client_id=str(settings.azure_client_id)) as credential,
        ContainerClient(
            settings.blob_account_url, settings.policies_container, credential=credential
        ) as container,
    ):
        yield container

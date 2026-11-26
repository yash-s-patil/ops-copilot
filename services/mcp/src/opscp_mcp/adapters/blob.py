import asyncio

import structlog
from azure.core.exceptions import AzureError, ResourceNotFoundError
from azure.storage.blob.aio import ContainerClient

from opscp_common.errors import BackendTimeoutError, BackendUnavailableError, InternalError

POLICY_EXTENSION = ".md"
_ENCODING = "utf-8"
_LOG = structlog.get_logger(__name__)


class BlobPolicies:
    def __init__(self, container: ContainerClient, *, timeout_s: int, max_bytes: int) -> None:
        self._container = container
        self._timeout_s = timeout_s
        self._max_bytes = max_bytes

    async def read(self, name: str) -> str | None:
        try:
            async with asyncio.timeout(self._timeout_s):
                content = await self._download(name)
        except TimeoutError as error:
            raise BackendTimeoutError("The policy store took too long to answer.") from error
        except ResourceNotFoundError:
            return None
        except AzureError as error:
            _LOG.error("policy_store_failed", policy=name, error=type(error).__name__)
            raise BackendUnavailableError("The policy store is unavailable.") from error
        try:
            return content.decode(_ENCODING)
        except UnicodeDecodeError as error:
            _LOG.error("policy_document_not_text", policy=name)
            raise InternalError from error

    async def _download(self, name: str) -> bytes:
        """The size is asked for first: a download buffers the blob before reporting it."""
        blob = self._container.get_blob_client(f"{name}{POLICY_EXTENSION}")
        size = (await blob.get_blob_properties()).size
        if size > self._max_bytes:
            _LOG.error("policy_document_too_large", policy=name, size=size)
            raise InternalError
        if size == 0:
            return b""
        download = await blob.download_blob(offset=0, length=self._max_bytes)
        return await download.readall()

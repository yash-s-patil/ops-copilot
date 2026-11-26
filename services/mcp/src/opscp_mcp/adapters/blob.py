import structlog
from azure.core.exceptions import (
    AzureError,
    ClientAuthenticationError,
    HttpResponseError,
    ResourceNotFoundError,
    ServiceRequestError,
    ServiceResponseError,
)
from azure.storage.blob.aio import ContainerClient

from opscp_common.errors import InternalError
from opscp_mcp.reliability.guard import Failure, Guard

POLICY_EXTENSION = ".md"
_ENCODING = "utf-8"
_TOO_MANY_REQUESTS = 429
_SERVER_ERRORS = 500
_REFUSED_IDENTITY = (401, 403)
_LOG = structlog.get_logger(__name__)


def classify(error: Exception) -> Failure | None:
    """A lost connection, a busy service or a server fault may pass. A refused identity
    stands until someone fixes it. Anything else is a request this server got wrong."""
    if isinstance(error, ServiceRequestError | ServiceResponseError):
        return Failure.PASSING
    if isinstance(error, ClientAuthenticationError):
        return Failure.STANDING
    if not isinstance(error, HttpResponseError) or error.status_code is None:
        return None
    if error.status_code == _TOO_MANY_REQUESTS or error.status_code >= _SERVER_ERRORS:
        return Failure.PASSING
    return Failure.STANDING if error.status_code in _REFUSED_IDENTITY else None


class BlobPolicies:
    def __init__(self, container: ContainerClient, guard: Guard, *, max_bytes: int) -> None:
        self._container = container
        self._guard = guard
        self._max_bytes = max_bytes

    async def read(self, name: str) -> str | None:
        try:
            content = await self._guard.run(lambda: self._download(name), retry=True)
        except AzureError as error:
            # A request the store would not accept, which is this server's mistake.
            _LOG.error("policy_store_refused", policy=name, error=type(error).__name__)
            raise InternalError from error
        if content is None:
            return None
        try:
            return content.decode(_ENCODING)
        except UnicodeDecodeError as error:
            _LOG.error("policy_document_not_text", policy=name)
            raise InternalError from error

    async def _download(self, name: str) -> bytes | None:
        """The size is asked for first: a download buffers the blob before reporting it."""
        blob = self._container.get_blob_client(f"{name}{POLICY_EXTENSION}")
        try:
            size = (await blob.get_blob_properties()).size
            if size > self._max_bytes:
                _LOG.error("policy_document_too_large", policy=name, size=size)
                raise InternalError
            if size == 0:
                return b""
            download = await blob.download_blob(offset=0, length=self._max_bytes)
            return await download.readall()
        except ResourceNotFoundError:
            return None

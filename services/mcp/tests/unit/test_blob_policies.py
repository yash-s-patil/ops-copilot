import asyncio
from dataclasses import dataclass

import pytest
from azure.core.exceptions import (
    ClientAuthenticationError,
    HttpResponseError,
    ResourceNotFoundError,
    ServiceRequestError,
)

from opscp_common.errors import BackendTimeoutError, BackendUnavailableError, InternalError
from opscp_mcp.adapters.blob import BlobPolicies

TIMEOUT_S = 1
MAX_BYTES = 100
HOST = "stopscpdemo.blob.core.windows.net"


@dataclass
class _Properties:
    size: int


class _Download:
    def __init__(self, content: bytes) -> None:
        self._content = content

    async def readall(self) -> bytes:
        return self._content


class _Blob:
    def __init__(self, content: bytes, failure: Exception | None, delay_s: float) -> None:
        self._content = content
        self._failure = failure
        self._delay_s = delay_s
        self.downloads: list[tuple[int, int]] = []

    async def get_blob_properties(self) -> _Properties:
        await asyncio.sleep(self._delay_s)
        if self._failure is not None:
            raise self._failure
        return _Properties(size=len(self._content))

    async def download_blob(self, *, offset: int, length: int) -> _Download:
        self.downloads.append((offset, length))
        return _Download(self._content[offset : offset + length])


class _Container:
    """Stands in for the SDK's container client: one blob, or one failure."""

    def __init__(
        self, content: bytes = b"", *, failure: Exception | None = None, delay_s: float = 0
    ) -> None:
        self.blob = _Blob(content, failure, delay_s)
        self.requested: list[str] = []

    def get_blob_client(self, name: str) -> _Blob:
        self.requested.append(name)
        return self.blob


def _policies(container: _Container) -> BlobPolicies:
    return BlobPolicies(container, timeout_s=TIMEOUT_S, max_bytes=MAX_BYTES)  # pyright: ignore[reportArgumentType] -- a stand-in for the SDK client


async def test_a_document_is_read_from_the_blob_named_after_it() -> None:
    container = _Container("नीति".encode())

    assert await _policies(container).read("refund-policy") == "नीति"
    assert container.requested == ["refund-policy.md"]


async def test_a_blob_that_does_not_exist_reads_as_none() -> None:
    container = _Container(failure=ResourceNotFoundError("BlobNotFound"))

    assert await _policies(container).read("refund-policy") is None


async def test_an_empty_blob_reads_as_empty_text() -> None:
    assert await _policies(_Container(b"")).read("refund-policy") == ""


@pytest.mark.parametrize(
    "failure",
    [
        ServiceRequestError(f"Cannot connect to host {HOST}:443 ssl:default"),
        HttpResponseError(f"AuthorizationFailure RequestId:abc on {HOST}"),
        ClientAuthenticationError(f"ManagedIdentityCredential failed for {HOST}"),
    ],
    ids=lambda failure: type(failure).__name__,
)
async def test_a_store_failure_is_reported_as_unavailable_without_its_details(
    failure: Exception,
) -> None:
    with pytest.raises(BackendUnavailableError) as fault:
        await _policies(_Container(failure=failure)).read("refund-policy")

    body = fault.value.body()
    assert (body.message, body.retryable, body.details) == (
        "The policy store is unavailable.",
        True,
        {},
    )


async def test_a_store_that_does_not_answer_in_time_is_a_timeout() -> None:
    with pytest.raises(BackendTimeoutError) as fault:
        await _policies(_Container(b"late", delay_s=TIMEOUT_S * 5)).read("refund-policy")

    assert fault.value.body().retryable


async def test_a_document_larger_than_the_limit_is_refused_without_being_downloaded() -> None:
    container = _Container(b"x" * (MAX_BYTES + 1))

    with pytest.raises(InternalError):
        await _policies(container).read("refund-policy")

    assert container.blob.downloads == []


async def test_a_document_of_exactly_the_limit_is_read_with_a_bounded_download() -> None:
    container = _Container(b"x" * MAX_BYTES)

    assert await _policies(container).read("refund-policy") == "x" * MAX_BYTES
    assert container.blob.downloads == [(0, MAX_BYTES)]


async def test_a_blob_that_is_not_text_is_a_server_fault() -> None:
    with pytest.raises(InternalError):
        await _policies(_Container(b"\xff\xfe\x00binary")).read("refund-policy")

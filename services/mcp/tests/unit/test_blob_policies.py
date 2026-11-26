import asyncio
from dataclasses import dataclass

import pytest
from azure.core.exceptions import (
    ClientAuthenticationError,
    HttpResponseError,
    ResourceNotFoundError,
    ServiceRequestError,
    ServiceResponseError,
)

from opscp_common.errors import BackendTimeoutError, BackendUnavailableError, InternalError
from opscp_mcp.adapters.blob import BlobPolicies, classify
from opscp_mcp.reliability.breaker import BreakerState
from opscp_mcp.reliability.guard import Guard
from opscp_mcp_testing.guards import RETRIES, a_guard

TIMEOUT_S = 1
ALWAYS = 1_000_000
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
    def __init__(
        self, content: bytes, failure: Exception | None, delay_s: float, failing_calls: int
    ) -> None:
        self._content = content
        self._failure = failure
        self._delay_s = delay_s
        self._failing_calls = failing_calls
        self.asked = 0
        self.downloads: list[tuple[int, int]] = []

    async def get_blob_properties(self) -> _Properties:
        self.asked += 1
        await asyncio.sleep(self._delay_s)
        if self._failure is not None and self.asked <= self._failing_calls:
            raise self._failure
        return _Properties(size=len(self._content))

    async def download_blob(self, *, offset: int, length: int) -> _Download:
        self.downloads.append((offset, length))
        return _Download(self._content[offset : offset + length])


class _Container:
    """Stands in for the SDK's container client: one blob, or one failure."""

    def __init__(
        self,
        content: bytes = b"",
        *,
        failure: Exception | None = None,
        delay_s: float = 0,
        failing_calls: int = ALWAYS,
    ) -> None:
        self.blob = _Blob(content, failure, delay_s, failing_calls)
        self.requested: list[str] = []

    def get_blob_client(self, name: str) -> _Blob:
        self.requested.append(name)
        return self.blob


def _answered(status: int) -> HttpResponseError:
    failure = HttpResponseError(f"status {status} from {HOST}")
    failure.status_code = status
    return failure


def _policies(container: _Container, guard: Guard | None = None) -> BlobPolicies:
    guard = guard or a_guard("blob", classify=classify, timeout_s=TIMEOUT_S)
    return BlobPolicies(container, guard, max_bytes=MAX_BYTES)  # pyright: ignore[reportArgumentType] -- a stand-in for the SDK client


async def test_a_document_is_read_from_the_blob_named_after_it() -> None:
    container = _Container("नीति".encode())

    assert await _policies(container).read("refund-policy") == "नीति"
    assert container.requested == ["refund-policy.md"]


async def test_a_blob_that_does_not_exist_reads_as_none() -> None:
    container = _Container(failure=ResourceNotFoundError("BlobNotFound"))

    assert await _policies(container).read("refund-policy") is None


async def test_an_empty_blob_reads_as_empty_text() -> None:
    assert await _policies(_Container(b"")).read("refund-policy") == ""


async def test_a_store_that_cannot_be_reached_is_unavailable_without_its_details() -> None:
    unreachable = ServiceRequestError(f"Cannot connect to host {HOST}:443 ssl:default")

    with pytest.raises(BackendUnavailableError) as fault:
        await _policies(_Container(failure=unreachable)).read("refund-policy")

    body = fault.value.body()
    assert (body.message, body.retryable, body.details) == (
        "A system this needs is unavailable. Try again shortly.",
        True,
        {},
    )


async def test_a_request_the_store_will_not_accept_is_a_defect_not_an_outage() -> None:
    with pytest.raises(InternalError) as fault:
        await _policies(_Container(failure=_answered(400))).read("refund-policy")

    body = fault.value.body()
    assert (body.retryable, body.details) == (False, {})
    assert HOST not in body.message


async def test_a_blob_removed_between_being_sized_and_being_read_reads_as_none() -> None:
    container = _Container(b"policy")

    async def removed(*, offset: int, length: int) -> _Download:
        raise ResourceNotFoundError("BlobNotFound")

    container.blob.download_blob = removed

    assert await _policies(container).read("refund-policy") is None


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


@pytest.mark.parametrize(
    "failure",
    [
        ServiceRequestError("connection refused"),
        ServiceResponseError("connection reset"),
        _answered(429),
        _answered(500),
        _answered(503),
    ],
    ids=["cannot connect", "connection lost", "429", "500", "503"],
)
async def test_a_failure_that_may_pass_is_tried_again_and_the_read_succeeds(
    failure: Exception,
) -> None:
    container = _Container(b"policy", failure=failure, failing_calls=RETRIES)

    assert await _policies(container).read("refund-policy") == "policy"
    assert container.blob.asked == RETRIES + 1


@pytest.mark.parametrize(
    "failure",
    [_answered(401), _answered(403), ClientAuthenticationError(f"no token for {HOST}")],
    ids=["401", "403", "no credential"],
)
async def test_a_refused_identity_is_not_tried_again_but_does_count_against_the_store(
    failure: Exception,
) -> None:
    container = _Container(failure=failure)
    guard = a_guard("blob", classify=classify)

    for _ in range(10):
        with pytest.raises(BackendUnavailableError) as fault:
            await _policies(container, guard).read("refund-policy")
        assert HOST not in fault.value.body().model_dump_json()

    assert container.blob.asked == 5
    assert guard.state is BreakerState.OPEN


async def test_a_request_the_store_will_not_accept_is_not_held_against_it() -> None:
    container = _Container(failure=_answered(400))
    guard = a_guard("blob", classify=classify)

    for _ in range(10):
        with pytest.raises(InternalError):
            await _policies(container, guard).read("refund-policy")

    assert (container.blob.asked, guard.state) == (10, BreakerState.CLOSED)


async def test_a_blob_that_does_not_exist_is_an_answer_not_a_failure() -> None:
    container = _Container(failure=ResourceNotFoundError("BlobNotFound"))
    guard = a_guard("blob", classify=classify)

    for _ in range(10):
        assert await _policies(container, guard).read("refund-policy") is None

    assert (container.blob.asked, guard.state) == (10, BreakerState.CLOSED)


async def test_a_store_that_keeps_failing_is_left_alone_after_a_while() -> None:
    container = _Container(failure=ServiceRequestError("connection refused"))
    guard = a_guard("blob", classify=classify)

    for _ in range(10):
        with pytest.raises(BackendUnavailableError):
            await _policies(container, guard).read("refund-policy")

    assert guard.state is BreakerState.OPEN
    assert container.blob.asked == 5

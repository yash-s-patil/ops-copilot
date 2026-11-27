"""The worker's HTTP client: what it sends, and how it reads every kind of answer."""

import asyncio
from typing import Any

import httpx2
import pytest

from opscp_payments.adapters.sim_client import HttpPaymentProvider
from opscp_payments.ports.payments import ProviderUnavailableError, Submission, Trouble
from opscp_payments.sim.app import build_app
from opscp_payments.sim.auth import CallerTokens, FixedKey
from opscp_payments.sim.store import Store
from opscp_payments_testing.keys import AUDIENCE, ISSUER, NOW, public_pem, signed, worker_claims

BASE = "http://payments.test"
SUBMISSION = Submission(refund_id=5001, order_id=4471, amount_minor=62000, currency="INR")
TIMEOUT_S = 0.2


class _Tokens:
    def __init__(self, token: str | None = None) -> None:
        self._token = token or signed(worker_claims())
        self.fail_with: Exception | None = None

    async def token(self) -> str:
        if self.fail_with is not None:
            raise self.fail_with
        return self._token


def _provider(handler: Any, tokens: _Tokens | None = None) -> HttpPaymentProvider:
    http = httpx2.AsyncClient(base_url=BASE, transport=httpx2.MockTransport(handler))
    return HttpPaymentProvider(http, tokens or _Tokens(), timeout_s=TIMEOUT_S)


def _trouble(raised: pytest.ExceptionInfo[ProviderUnavailableError]) -> Trouble:
    return raised.value.trouble


async def test_a_submission_carries_the_key_the_worker_token_and_only_the_order() -> None:
    seen: list[httpx2.Request] = []

    def accept(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(202, json={"provider_ref": "sim_1", "status": "processing"})

    tokens = _Tokens("the-workers-token")
    accepted = await _provider(accept, tokens).submit(SUBMISSION)

    (request,) = seen
    assert accepted.provider_ref == "sim_1"
    assert (request.method, str(request.url)) == ("POST", f"{BASE}/refunds")
    assert request.headers["Idempotency-Key"] == "refund-5001"
    assert request.headers["Authorization"] == "Bearer the-workers-token"
    assert "X-Sim-Forced-Outcome" not in request.headers
    assert httpx2.Response(200, content=request.content).json() == {
        "order_id": 4471,
        "amount_minor": 62000,
        "currency": "INR",
    }


async def test_a_drill_is_marked_in_a_header_and_not_in_the_order() -> None:
    seen: list[httpx2.Request] = []

    def accept(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(202, json={"provider_ref": "sim_1"})

    await _provider(accept).submit(SUBMISSION.model_copy(update={"forced_outcome": "decline"}))

    assert seen[0].headers["X-Sim-Forced-Outcome"] == "decline"
    assert b"decline" not in seen[0].content


@pytest.mark.parametrize(
    ("answer", "status", "reason"),
    [
        ({"provider_ref": "sim_1", "status": "processing"}, "processing", None),
        ({"provider_ref": "sim_1", "status": "succeeded", "reason_code": None}, "succeeded", None),
        (
            {"status": "declined", "reason_code": "provider_declined"},
            "declined",
            "provider_declined",
        ),
        ({"status": "succeeded", "something_new": {"a": 1}}, "succeeded", None),
    ],
)
async def test_a_status_answer_is_read(answer: Any, status: str, reason: str | None) -> None:
    seen: list[str] = []

    def answered(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.url.raw_path.decode())
        return httpx2.Response(200, json=answer)

    settlement = await _provider(answered).status("sim_1")

    assert (settlement.status, settlement.reason_code) == (status, reason)
    assert seen == ["/refunds/sim_1"]


async def test_a_reference_is_sent_as_one_path_segment_whatever_it_contains() -> None:
    seen: list[str] = []

    def answered(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.url.raw_path.decode())
        return httpx2.Response(200, json={"status": "processing"})

    await _provider(answered).status("../admin?x=1#y")

    assert seen == ["/refunds/..%2Fadmin%3Fx%3D1%23y"]


@pytest.mark.parametrize(
    ("status", "trouble"),
    [
        (429, "rate_limited"),
        (500, "server_error"),
        (502, "server_error"),
        (503, "server_error"),
        (400, "refused"),
        (401, "refused"),
        (403, "refused"),
        (404, "refused"),
        (409, "refused"),
        (200, "refused"),
        (301, "refused"),
    ],
)
async def test_an_answer_that_accepts_nothing_is_a_trouble_and_its_words_are_dropped(
    status: int, trouble: str
) -> None:
    def refuse(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            status, text="Customer Asha Rao, card 4111…", headers={"Location": "/x"}
        )

    with pytest.raises(ProviderUnavailableError) as raised:
        await _provider(refuse).submit(SUBMISSION)

    assert _trouble(raised) == trouble
    assert "Asha" not in str(raised.value)


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"not json",
        b"[]",
        b"{}",
        b'{"provider_ref": ""}',
        b'{"provider_ref": 7}',
        b'{"provider_ref": ".."}',
        b'{"provider_ref": "sim/1"}',
        b'{"provider_ref": "sim 1"}',
        b'{"provider_ref": "Asha Rao +91 90000 00001"}',
        b'{"provider_ref": "sim_1\n"}',
    ],
)
async def test_an_acceptance_that_cannot_be_read_is_an_unexpected_answer(body: bytes) -> None:
    def accept(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(202, content=body)

    with pytest.raises(ProviderUnavailableError) as raised:
        await _provider(accept).submit(SUBMISSION)

    assert _trouble(raised) == "unexpected_answer"


async def test_a_reference_too_long_to_store_is_an_unexpected_answer() -> None:
    def accept(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(202, json={"provider_ref": "x" * 129})

    with pytest.raises(ProviderUnavailableError) as raised:
        await _provider(accept).submit(SUBMISSION)

    assert _trouble(raised) == "unexpected_answer"


async def test_a_status_the_worker_does_not_know_is_an_unexpected_answer() -> None:
    def answered(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"status": "refunded_twice"})

    with pytest.raises(ProviderUnavailableError) as raised:
        await _provider(answered).status("sim_1")

    assert _trouble(raised) == "unexpected_answer"


@pytest.mark.parametrize(
    ("error", "trouble"),
    [
        (httpx2.ConnectError("refused"), "unreachable"),
        (httpx2.UnsupportedProtocol("ftp"), "unreachable"),
        (httpx2.ReadError("reset"), "connection_lost"),
        (httpx2.WriteError("reset"), "connection_lost"),
        (httpx2.RemoteProtocolError("garbage"), "connection_lost"),
        (httpx2.ConnectTimeout("slow"), "timeout"),
        (httpx2.ReadTimeout("slow"), "timeout"),
        (httpx2.PoolTimeout("busy"), "timeout"),
        (httpx2.DecodingError("bad gzip"), "unexpected_answer"),
        (httpx2.TooManyRedirects("loop"), "unexpected_answer"),
    ],
)
async def test_a_failure_to_reach_the_provider_is_a_trouble(error: Exception, trouble: str) -> None:
    def fail(_request: httpx2.Request) -> httpx2.Response:
        raise error

    with pytest.raises(ProviderUnavailableError) as raised:
        await _provider(fail).status("sim_1")

    assert _trouble(raised) == trouble


async def test_a_provider_that_never_answers_is_given_up_on_after_the_time_allowed() -> None:
    async def hang(_request: httpx2.Request) -> httpx2.Response:
        await asyncio.sleep(30)
        return httpx2.Response(202)

    with pytest.raises(ProviderUnavailableError) as raised:
        await _provider(hang).submit(SUBMISSION)

    assert _trouble(raised) == "timeout"


async def test_a_redirect_is_not_followed() -> None:
    asked: list[str] = []

    def redirect(request: httpx2.Request) -> httpx2.Response:
        asked.append(str(request.url))
        return httpx2.Response(307, headers={"Location": "http://elsewhere.test/refunds"})

    with pytest.raises(ProviderUnavailableError):
        await _provider(redirect).submit(SUBMISSION)

    assert asked == [f"{BASE}/refunds"]


async def test_the_client_and_the_simulator_understand_each_other() -> None:
    seconds = [0.0]
    store = Store(settle_delay_s=3, capacity=100, clock=lambda: seconds[0])
    callers = CallerTokens(
        FixedKey(public_pem()),
        issuer=ISSUER,
        audience=AUDIENCE,
        tenant_id=None,
        clock_skew_s=60,
        now=lambda: NOW,
    )
    app = build_app(store, callers, allow_forced_outcome=True)
    http = httpx2.AsyncClient(base_url=BASE, transport=httpx2.ASGITransport(app=app))
    provider = HttpPaymentProvider(http, _Tokens(), timeout_s=5)

    accepted = await provider.submit(SUBMISSION)
    again = await provider.submit(SUBMISSION)
    processing = await provider.status(accepted.provider_ref)
    seconds[0] = 3
    succeeded = await provider.status(accepted.provider_ref)
    drill = SUBMISSION.model_copy(update={"refund_id": 5002, "forced_outcome": "decline"})
    marked = await provider.submit(drill)
    seconds[0] = 6
    declined = await provider.status(marked.provider_ref)

    assert again == accepted
    assert (processing.status, succeeded.status) == ("processing", "succeeded")
    assert (declined.status, declined.reason_code) == ("declined", "provider_declined")
    with pytest.raises(ProviderUnavailableError) as raised:
        await provider.status("sim_unknown")
    assert _trouble(raised) == "refused"

"""The payment provider over HTTP. Written for the simulator; a real provider needs its own.

Anything that is not a clear answer is reported as the provider being unavailable, with a
word for why. Nothing the provider wrote in an error is kept: it could say anything.
"""

import asyncio
from urllib.parse import quote

import httpx2
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from opscp_payments.ports.payments import (
    Accepted,
    AccessTokens,
    ProviderUnavailableError,
    Settlement,
    SettlementStatus,
    Submission,
)

IDEMPOTENCY_HEADER = "Idempotency-Key"
FORCED_OUTCOME_HEADER = "X-Sim-Forced-Outcome"
REFUNDS_PATH = "/refunds"
_ACCEPTED = 202
_OK = 200
_TOO_MANY = 429
_SERVER_ERRORS = 500
# Stored, logged and put in an address: letters, digits, hyphens and underscores only.
_REFERENCE = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"


class _Answer(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class _AcceptedAnswer(_Answer):
    provider_ref: str = Field(pattern=_REFERENCE)


class _StatusAnswer(_Answer):
    status: SettlementStatus
    reason_code: str | None = None


class HttpPaymentProvider:
    def __init__(self, http: httpx2.AsyncClient, tokens: AccessTokens, *, timeout_s: float) -> None:
        """`http` has the provider's address as its base URL. `timeout_s` bounds a whole call."""
        self._http = http
        self._tokens = tokens
        self._timeout_s = timeout_s

    async def submit(self, submission: Submission) -> Accepted:
        headers = {IDEMPOTENCY_HEADER: submission.idempotency_key}
        if submission.forced_outcome is not None:
            headers[FORCED_OUTCOME_HEADER] = submission.forced_outcome
        order = submission.model_dump(include={"order_id", "amount_minor", "currency"})
        request = self._http.build_request("POST", REFUNDS_PATH, json=order, headers=headers)
        answer = await self._ask(request, expecting=_ACCEPTED)
        return Accepted(provider_ref=_read(_AcceptedAnswer, answer).provider_ref)

    async def status(self, provider_ref: str) -> Settlement:
        request = self._http.build_request("GET", f"{REFUNDS_PATH}/{quote(provider_ref, safe='')}")
        said = _read(_StatusAnswer, await self._ask(request, expecting=_OK))
        return Settlement(status=said.status, reason_code=said.reason_code)

    async def _ask(self, request: httpx2.Request, *, expecting: int) -> httpx2.Response:
        try:
            async with asyncio.timeout(self._timeout_s):
                request.headers["Authorization"] = f"Bearer {await self._tokens.token()}"
                answer = await self._http.send(request)
        except (TimeoutError, httpx2.TimeoutException) as error:
            raise ProviderUnavailableError("timeout") from error
        except (httpx2.ConnectError, httpx2.UnsupportedProtocol) as error:
            raise ProviderUnavailableError("unreachable") from error
        except httpx2.TransportError as error:
            # The request may have arrived before the connection went.
            raise ProviderUnavailableError("connection_lost") from error
        except httpx2.HTTPError as error:
            raise ProviderUnavailableError("unexpected_answer") from error
        if answer.status_code == expecting:
            return answer
        if answer.status_code == _TOO_MANY:
            raise ProviderUnavailableError("rate_limited")
        if answer.status_code >= _SERVER_ERRORS:
            raise ProviderUnavailableError("server_error")
        raise ProviderUnavailableError("refused")


def _read[T: _Answer](shape: type[T], answer: httpx2.Response) -> T:
    try:
        return shape.model_validate_json(answer.content)
    except ValidationError as error:
        raise ProviderUnavailableError("unexpected_answer") from error

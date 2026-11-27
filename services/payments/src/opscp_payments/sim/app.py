"""A stand-in for a payment provider. No money moves.

It accepts a refund, says `processing` for a few seconds and then `succeeded`. A repeated
idempotency key gets the first result, which is the property the worker relies on to never
pay twice. It refuses to start in production.
"""

import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated

import httpx2
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from opscp_common.limits import load_limits
from opscp_common.logging import configure_logging
from opscp_payments.adapters.sim_client import (
    FORCED_OUTCOME_HEADER,
    IDEMPOTENCY_HEADER,
    REFUNDS_PATH,
)
from opscp_payments.ports.payments import SettlementStatus
from opscp_payments.settings import SIM_NAME, SimSettings
from opscp_payments.sim.auth import CallerTokens, KeysUnavailableError, caller_tokens
from opscp_payments.sim.store import DECLINE_REASON, KeyReusedError, Order, Refund, Store

HEALTH_PATH = "/healthz"
_BEARER = "bearer "
_KEY = r"^[A-Za-z0-9_-]{1,64}$"
_CURRENCY = r"^[A-Z]{3}$"
_DECLINE = "decline"
_UNAUTHORISED = 401
_FORBIDDEN = 403
_NOT_FOUND = 404
_CONFLICT = 409
_UNAVAILABLE = 503
_INVALID = 422


class RefundOrder(BaseModel):
    """All that a provider is told. A field carrying a name or a contact detail is refused."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    order_id: int = Field(gt=0)
    amount_minor: int = Field(gt=0)
    currency: str = Field(pattern=_CURRENCY)


class RefundStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider_ref: str
    status: SettlementStatus
    reason_code: str | None = None


async def _the_worker(request: Request) -> None:
    """Lets the request through only with a token this service accepts as the worker's."""
    callers: CallerTokens = request.app.state.callers
    header = request.headers.get("authorization", "")
    token = header[len(_BEARER) :] if header.lower().startswith(_BEARER) else ""
    try:
        verdict = await callers.verdict(token) if token else "unknown"
    except KeysUnavailableError as error:
        raise HTTPException(_UNAVAILABLE) from error
    if verdict == "unknown":
        raise HTTPException(_UNAUTHORISED, headers={"WWW-Authenticate": "Bearer"})
    if verdict == "not_allowed":
        raise HTTPException(_FORBIDDEN)


def _status_of(store: Store, refund: Refund) -> RefundStatus:
    status = store.status(refund)
    return RefundStatus(
        provider_ref=refund.provider_ref,
        status=status,
        reason_code=DECLINE_REASON if status == "declined" else None,
    )


async def _invalid(_request: Request, _error: Exception) -> JSONResponse:
    """The framework's own answer would repeat what the caller sent."""
    return JSONResponse({"detail": "invalid_request"}, status_code=_INVALID)


def build_app(store: Store, callers: CallerTokens, *, allow_forced_outcome: bool) -> FastAPI:
    app = FastAPI(title=SIM_NAME, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.callers = callers
    app.add_exception_handler(RequestValidationError, _invalid)
    worker_only = [Depends(_the_worker)]

    @app.get(HEALTH_PATH)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(REFUNDS_PATH, status_code=202, dependencies=worker_only)
    async def submit(
        order: RefundOrder,
        key: Annotated[str, Header(alias=IDEMPOTENCY_HEADER, pattern=_KEY)],
        forced: Annotated[str | None, Header(alias=FORCED_OUTCOME_HEADER)] = None,
    ) -> RefundStatus:
        decline = allow_forced_outcome and forced == _DECLINE
        try:
            refund = store.accept(key, Order(**order.model_dump()), decline=decline)
        except KeyReusedError as error:
            raise HTTPException(_CONFLICT, "idempotency_key_reused") from error
        return _status_of(store, refund)

    @app.get(REFUNDS_PATH + "/{provider_ref}", dependencies=worker_only)
    async def status(provider_ref: str) -> RefundStatus:
        refund = store.find(provider_ref)
        if refund is None:
            raise HTTPException(_NOT_FOUND)
        return _status_of(store, refund)

    return app


def create_app() -> FastAPI:
    """Entry point for the web server. Reads and checks every setting before serving."""
    settings = SimSettings()  # pyright: ignore[reportCallIssue] -- fields come from the environment
    configure_logging(service=SIM_NAME, level=settings.log_level, json=settings.json_logs)
    limits = load_limits(settings.limits_file)
    http = httpx2.AsyncClient(timeout=limits.sim.keys_timeout_s, trust_env=False)
    store = Store(
        settle_delay_s=limits.sim.settle_delay_s,
        capacity=limits.sim.refunds_kept,
        clock=time.monotonic,
    )
    app = build_app(
        store,
        caller_tokens(settings.callers(), http, limits.sim),
        allow_forced_outcome=settings.sim_allow_forced_outcome,
    )

    @asynccontextmanager
    async def close_http(_app: FastAPI) -> AsyncGenerator[None]:
        yield
        await http.aclose()

    app.router.lifespan_context = close_http
    return app

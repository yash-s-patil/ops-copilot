"""The routes a person's decision arrives through. No tool can reach them.

They take the same bearer token as the MCP endpoint and are answered in the same error
body. A decision is counted against the caller's rate limit and written to the audit log,
whether it was taken or refused.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Annotated, Any, NoReturn
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from fastmcp.server.auth import AuthProvider
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from opscp_common.errors import (
    ErrorCode,
    OpsError,
    RateLimitedError,
    ValidationFailedError,
)
from opscp_common.hashing import JsonValue
from opscp_common.limits import Limits
from opscp_mcp.authz.identity import Identity, identity_from_claims
from opscp_mcp.container import Services
from opscp_mcp.middleware.envelope import defect
from opscp_mcp.ports.controls import AuditAction, AuditEntry
from opscp_mcp.reliability.deadline import within
from opscp_mcp.services.approvals import (
    ActionView,
    ApprovalToken,
    Approved,
    EntryNow,
    Recorded,
)
from opscp_mcp.services.rate import count_request
from opscp_mcp.services.redaction import redacted

_LOG = structlog.get_logger(__name__)
_BEARER = "bearer "
_MS_PER_S = 1000
_CANCELLED = "CANCELLED"
_HASH = r"^[0-9a-f]{64}$"
_STATUS: dict[ErrorCode, int] = {
    ErrorCode.PERMISSION_DENIED: 403,
    ErrorCode.VALIDATION_FAILED: 422,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.POLICY_VIOLATION: 409,
    ErrorCode.APPROVAL_INVALID: 409,
    ErrorCode.IDEMPOTENCY_CONFLICT: 409,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.BACKEND_UNAVAILABLE: 503,
    ErrorCode.TIMEOUT: 504,
    ErrorCode.INTERNAL: 500,
}
UNAUTHORISED = 401


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Approve(_Body):
    args_hash: str = Field(pattern=_HASH)


class Reject(_Body):
    reason: str = Field(min_length=1)


class Waiting(_Body):
    items: tuple[ActionView, ...]


class NotSignedInError(Exception):
    """The request carried no token this server accepts."""


@dataclass(frozen=True, slots=True)
class Access:
    """What the routes need to know a caller: kept on the application, set at start-up."""

    sign_in: AuthProvider
    limits: Limits


def services_of(request: Request) -> Services:
    return request.app.state.services


def _access(request: Request) -> Access:
    return request.app.state.access


def _answer(error: Exception, *, during: str) -> JSONResponse:
    """Our own errors are told to the caller. Anything else is a defect and says only that."""
    if isinstance(error, OpsError):
        _LOG.info("request_refused", during=during, code=error.code.value)
        body = error.body()
    else:
        body = defect(error, during=during)
    response = JSONResponse(body.model_dump(mode="json"), status_code=_STATUS[body.code])
    if isinstance(error, RateLimitedError):
        response.headers["Retry-After"] = str(body.details["retry_after_seconds"])
    return response


class Answered(APIRoute):
    """Runs a route within the time allowed and answers every failure in the one error body.

    A failure is answered here, inside the application, so that the web server never sees
    it: its own handling would write the exception's text to the log.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handle = super().get_route_handler()

        async def answered(request: Request) -> Response:
            limits = _access(request).limits
            try:
                return await within(limits.approval.route_timeout_s, handle(request))
            except (NotSignedInError, RequestValidationError):
                raise
            except Exception as error:  # noqa: BLE001 -- answered in the one error body
                return _answer(error, during=self.path)

        return answered


async def caller(request: Request) -> Identity:
    """The signed-in person, counted against their rate limit."""
    access = _access(request)
    header = request.headers.get("authorization", "")
    token = header[len(_BEARER) :] if header.lower().startswith(_BEARER) else ""
    verified = await access.sign_in.verify_token(token) if token else None
    if verified is None:
        raise NotSignedInError
    identity = identity_from_claims(verified.claims)
    await count_request(services_of(request).rates, identity, access.limits.timeout.postgres_s)
    return identity


SignedIn = Annotated[Identity, Depends(caller)]


@dataclass(frozen=True, slots=True)
class _Decision:
    action: AuditAction
    target: str
    arguments: dict[str, JsonValue]


def _entry(
    request: Request,
    identity: Identity,
    decision: _Decision,
    started: float,
    failure: BaseException | None = None,
) -> AuditEntry:
    limits = _access(request).limits
    code = None if failure is None else _code_of(failure)
    denied = code == ErrorCode.PERMISSION_DENIED.value
    return AuditEntry(
        actor_oid=identity.oid,
        actor_roles=tuple(sorted(identity.roles)),
        actor_kind="user",
        target=decision.target,
        action="deny" if denied else decision.action,
        args_redacted=redacted(
            decision.arguments, decision.arguments.keys(), limits.audit.text_chars
        ),
        outcome="denied" if denied else "ok" if code is None else "error",
        error_code=code,
        duration_ms=round((time.monotonic() - started) * _MS_PER_S),
    )


async def _decided[T](
    request: Request,
    identity: Identity,
    decision: _Decision,
    decide: Callable[[EntryNow], Awaitable[Recorded[T]]],
) -> T:
    """Takes a decision. Its audit entry is stored with it, or a refusal is recorded after."""
    started = time.monotonic()
    taken: list[AuditEntry] = []

    def entry_now() -> AuditEntry:
        taken.append(_entry(request, identity, decision, started))
        return taken[-1]

    failure: BaseException | None = None
    recorded: Recorded[T] | None = None
    try:
        recorded = await decide(entry_now)
    except (Exception, asyncio.CancelledError) as error:  # noqa: BLE001 -- recorded, then raised again
        failure = error
    if recorded is None or failure is not None:
        return await _refused(request, identity, decision, started, failure)
    _LOG.info("audit", audit_id=recorded.audit_id, **taken[-1].model_dump(mode="json"))
    return recorded.outcome


async def _audited[T](
    request: Request, identity: Identity, decision: _Decision, call: Callable[[], Awaitable[T]]
) -> T:
    """Makes a call that stores nothing itself, and records it whatever came of it."""
    started = time.monotonic()
    failure: BaseException | None = None
    result: T | None = None
    try:
        result = await call()
    except (Exception, asyncio.CancelledError) as error:  # noqa: BLE001 -- recorded, then raised again
        failure = error
    if result is None or failure is not None:
        return await _refused(request, identity, decision, started, failure)
    await asyncio.shield(_record(request, _entry(request, identity, decision, started)))
    return result


async def _refused(
    request: Request,
    identity: Identity,
    decision: _Decision,
    started: float,
    failure: BaseException | None,
) -> NoReturn:
    """Records what went wrong and raises it. A client that has gone away cannot stop this."""
    failure = failure or AssertionError()
    entry = _entry(request, identity, decision, started, failure)
    await asyncio.shield(_record(request, entry))
    raise failure


async def _record(request: Request, entry: AuditEntry) -> None:
    limits = _access(request).limits
    fields = entry.model_dump(mode="json")
    try:
        audit_id = await within(limits.timeout.postgres_s, services_of(request).audit.record(entry))
    except Exception:
        # The log stream keeps what the table did not.
        _LOG.error("audit_unwritten", **fields)
        raise
    _LOG.info("audit", audit_id=audit_id, **fields)


def _code_of(failure: BaseException) -> str:
    if isinstance(failure, asyncio.CancelledError):
        return _CANCELLED
    return (failure.code if isinstance(failure, OpsError) else ErrorCode.INTERNAL).value


APPROVALS_PATH = "/approvals"
router = APIRouter(prefix=APPROVALS_PATH, route_class=Answered)


@router.get("")
async def waiting(
    request: Request, identity: SignedIn, ticket_id: Annotated[int | None, Query(gt=0)] = None
) -> Waiting:
    """What the caller asked for and has not seen through, and what waits for them."""
    return Waiting(items=await services_of(request).approvals.waiting(identity, ticket_id))


@router.get("/{action_id}")
async def one(request: Request, identity: SignedIn, action_id: UUID) -> ActionView:
    return await services_of(request).approvals.get(identity, action_id)


@router.post("/{action_id}/approve")
async def approve(request: Request, identity: SignedIn, action_id: UUID, body: Approve) -> Approved:
    approvals = services_of(request).approvals
    decision = _Decision(
        "approve",
        "POST /approvals/{id}/approve",
        {"action_id": str(action_id), "args_hash": body.args_hash},
    )
    return await _decided(
        request,
        identity,
        decision,
        lambda entry: approvals.approve(identity, action_id, body.args_hash, entry),
    )


@router.post("/{action_id}/reject")
async def reject(request: Request, identity: SignedIn, action_id: UUID, body: Reject) -> ActionView:
    approvals = services_of(request).approvals
    decision = _Decision(
        "reject",
        "POST /approvals/{id}/reject",
        {"action_id": str(action_id), "reason": body.reason},
    )
    return await _decided(
        request,
        identity,
        decision,
        lambda entry: approvals.reject(identity, action_id, body.reason, entry),
    )


@router.post("/{action_id}/token")
async def token(request: Request, identity: SignedIn, action_id: UUID) -> ApprovalToken:
    approvals = services_of(request).approvals
    decision = _Decision("call", "POST /approvals/{id}/token", {"action_id": str(action_id)})
    return await _audited(request, identity, decision, lambda: approvals.token(identity, action_id))


def invalid_request(_request: Request, _error: RequestValidationError) -> JSONResponse:
    """The framework's own answer would repeat what the caller sent."""
    body = ValidationFailedError("The request does not match what this route accepts.").body()
    return JSONResponse(body.model_dump(mode="json"), status_code=_STATUS[body.code])


def unreadable(_request: Request, error: StarletteHTTPException) -> JSONResponse:
    """The framework's own refusal of a body it could not parse, in our error body."""
    body = ValidationFailedError("The request could not be read.").body()
    return JSONResponse(body.model_dump(mode="json"), status_code=error.status_code)

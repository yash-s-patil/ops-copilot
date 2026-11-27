"""What every route shares: who is calling, and how a failure is answered.

Every route but the two probes needs a token issued for this service. Failures are
answered inside the application in the one error body, so that the web server never writes
an exception's text to the log.
"""

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Annotated, Any

import structlog
from fastapi import Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException

from opscp_agent.auth.obo import ReauthRequiredError
from opscp_agent.auth.verifier import Caller, Callers
from opscp_agent.events.bus import Bus
from opscp_agent.mcp_client.client import McpGateway
from opscp_agent.mcp_client.types import McpCallError, McpUnavailableError, ToolOutcome
from opscp_agent.runs.manager import RunManager
from opscp_agent.runs.store import RunStore
from opscp_agent.usage.cost import CostEstimate
from opscp_common.errors import (
    BackendUnavailableError,
    ErrorBody,
    ErrorCode,
    InternalError,
    OpsError,
    PermissionDeniedError,
    ValidationFailedError,
)
from opscp_common.failures import failure_site
from opscp_common.limits import Limits

_LOG = structlog.get_logger(__name__)
_BEARER = "bearer "
UNAUTHORISED = 401
STATUS: dict[ErrorCode, int] = {
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


BIGGEST_ID = 2**53 - 1


class Shape(BaseModel):
    """What a route answers with."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Body(BaseModel):
    """What a caller sends. Nothing is converted: a number sent as text is refused."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class NotSignedInError(Exception):
    """The request carried no token this service accepts, or one that has run out."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Agent:
    """What the routes work with. Built once at start-up and kept on the application."""

    callers: Callers
    gateway: McpGateway
    manager: RunManager
    store: RunStore
    bus: Bus
    cost: CostEstimate
    limits: Limits
    demo_tools: bool
    route_timeout_s: int


def agent_of(request: Request) -> Agent:
    return request.app.state.agent


async def caller(request: Request) -> Caller:
    header = request.headers.get("authorization", "")
    token = header[len(_BEARER) :] if header.lower().startswith(_BEARER) else ""
    found = await agent_of(request).callers.caller_of(token)
    if found is None:
        raise NotSignedInError
    return found


async def admin(request: Request) -> Caller:
    """Admin routes are for the platform administrator's role and no other."""
    found = await caller(request)
    if not found.person.is_admin:
        raise PermissionDeniedError("Your role does not allow this.")
    return found


SignedIn = Annotated[Caller, Depends(caller)]
Admin = Annotated[Caller, Depends(admin)]


def _body_of(error: Exception, *, during: str) -> tuple[ErrorBody, int]:
    match error:
        case McpCallError():
            return error.body, error.status or STATUS[error.body.code]
        case OpsError():
            return error.body(), STATUS[error.code]
        case McpUnavailableError():
            body = BackendUnavailableError("Meridian's systems could not be reached.").body()
            return body, STATUS[body.code]
        case _:
            _LOG.error("request_failed", during=during, **failure_site(error))
            body = InternalError().body()
            return body, STATUS[body.code]


def answer(error: Exception, *, during: str) -> JSONResponse:
    body, status = _body_of(error, during=during)
    if not isinstance(error, McpUnavailableError | OpsError | McpCallError):
        return JSONResponse(body.model_dump(mode="json"), status_code=status)
    _LOG.info("request_refused", during=during, code=body.code.value)
    response = JSONResponse(body.model_dump(mode="json"), status_code=status)
    retry_after = body.details.get("retry_after_seconds")
    if body.code == ErrorCode.RATE_LIMITED and isinstance(retry_after, int):
        response.headers["Retry-After"] = str(retry_after)
    return response


class Answered(APIRoute):
    """Runs a route within the time allowed and answers every failure in the one error body."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handle = super().get_route_handler()

        async def answered(request: Request) -> Response:
            try:
                async with asyncio.timeout(agent_of(request).route_timeout_s):
                    return await handle(request)
            except (NotSignedInError, RequestValidationError):
                raise
            except ReauthRequiredError as expired:
                raise NotSignedInError from expired
            except TimeoutError:
                body = BackendUnavailableError("That took too long. Try again.").body()
                return JSONResponse(body.model_dump(mode="json"), status_code=STATUS[body.code])
            except Exception as error:  # noqa: BLE001 -- answered in the one error body
                return answer(error, during=self.path)

        return answered


def unauthorised(_request: Request, _error: Exception) -> Response:
    return Response(status_code=UNAUTHORISED, headers={"WWW-Authenticate": "Bearer"})


def invalid_request(_request: Request, _error: Exception) -> JSONResponse:
    """The framework's own answer would repeat what the caller sent."""
    body = ValidationFailedError("The request does not match what this route accepts.").body()
    return JSONResponse(body.model_dump(mode="json"), status_code=STATUS[body.code])


def unreadable(_request: Request, error: Exception) -> JSONResponse:
    status = error.status_code if isinstance(error, StarletteHTTPException) else 400
    body = ValidationFailedError("The request could not be read.").body()
    return JSONResponse(body.model_dump(mode="json"), status_code=status)


def data_of(outcome: ToolOutcome) -> dict[str, Any]:
    """A tool's result, or the server's refusal raised as the route's own."""
    if outcome.error is not None or outcome.data is None:
        raise McpCallError(0, outcome.error or InternalError().body())
    return outcome.data

"""Refuses a request body larger than these routes ever need, before it is read.

The routes beside the MCP endpoint take a few short fields. Without a limit, anyone who can
reach the server could make it hold and parse a body of any size before it has even checked
who they are.
"""

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from opscp_common.errors import ValidationFailedError

_HTTP = "http"
_TOO_LARGE = 413
_LENGTH_REQUIRED = 411
_WITH_BODY = frozenset({"POST", "PUT", "PATCH"})


class SmallBodies:
    def __init__(self, app: ASGIApp, *, under: str, max_bytes: int) -> None:
        self._app = app
        self._under = under
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        guarded = (
            scope["type"] == _HTTP
            and scope["method"] in _WITH_BODY
            and scope["path"].startswith(self._under)
        )
        if guarded:
            headers = Headers(scope=scope)
            declared = headers.get("content-length")
            # With neither header there is no body. A body sent in chunks has no stated size.
            if "transfer-encoding" in headers or (
                declared is not None and not declared.isdecimal()
            ):
                await _refusal(_LENGTH_REQUIRED)(scope, receive, send)
                return
            if declared is not None and int(declared) > self._max_bytes:
                await _refusal(_TOO_LARGE)(scope, receive, send)
                return
        await self._app(scope, receive, send)


def _refusal(status: int) -> JSONResponse:
    body = ValidationFailedError("The request body is missing its length or is too large.").body()
    return JSONResponse(body.model_dump(mode="json"), status_code=status)

"""Refuses requests that a web page made.

A browser names the page it is acting for in the `Origin` header. The callers of this server
are programs, which send none, so a request that carries one is answered 403 whatever it
names. This is the origin check the MCP transport asks of a server.
"""

from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_HTTP = "http"
_ORIGIN = "origin"
_FORBIDDEN = 403


class NoBrowserOrigins:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == _HTTP and _ORIGIN in Headers(scope=scope):
            refusal = PlainTextResponse("Forbidden origin", status_code=_FORBIDDEN)
            await refusal(scope, receive, send)
            return
        await self._app(scope, receive, send)

"""The MCP client. It reaches the server as the signed-in person and as nobody else.

Each request carries a token for the MCP server that was exchanged for the person's own.
When the server answers 401 the token is exchanged again and the same request is sent once
more; a second 401 means the person must sign in again.
"""

import asyncio
import json
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, NoReturn, cast
from uuid import UUID

import anyio
import httpx2
import structlog
from fastmcp import Client
from fastmcp.client.client import CallToolResult
from fastmcp.client.transports import StreamableHttpTransport
from mcp.shared.exceptions import MCPError
from mcp.types import TextContent, TextResourceContents
from pydantic import ValidationError

from opscp_agent.auth.obo import ReauthRequiredError, TokenClient, TokenServiceError
from opscp_agent.jsonish import as_object
from opscp_agent.mcp_client.types import (
    Json,
    McpCallError,
    McpSession,
    McpUnavailableError,
    Reference,
    ToolOutcome,
    ToolSpec,
)
from opscp_common.errors import ErrorBody, ErrorCode, InternalError
from opscp_common.failures import failure_site

MCP_PATH = "/mcp"
RUN_HEADER = "X-Opscp-Run-Id"
VERSION_KEY = "opscp/version"
_UNAUTHORISED = 401
_MS_PER_S = 1000
_BEARER = "Bearer "
_LOG = structlog.get_logger(__name__)

type HttpFactory = Callable[..., httpx2.AsyncClient]

# How a connection that failed can surface from the MCP client. It runs its transport in
# task groups, so one failure may arrive alone or wrapped in a group, and it reports a
# session that closed under it as a RuntimeError.
_CONNECTION_FAILURES = (
    MCPError,
    httpx2.HTTPError,
    OSError,
    TimeoutError,
    anyio.ClosedResourceError,
    anyio.BrokenResourceError,
    RuntimeError,
)
_SIGN_IN_FAILURES = (ReauthRequiredError, TokenServiceError)
_MAY_BE_THE_CONNECTION = (*_CONNECTION_FAILURES, *_SIGN_IN_FAILURES, ExceptionGroup)


def _only_the_connection(error: BaseException) -> bool:
    """Whether every failure in this error, however grouped, is the connection failing."""
    if isinstance(error, BaseExceptionGroup):
        grouped = cast("BaseExceptionGroup[BaseException]", error)
        return all(_only_the_connection(inner) for inner in grouped.exceptions)
    return isinstance(error, (*_CONNECTION_FAILURES, *_SIGN_IN_FAILURES))


@dataclass(slots=True)
class _CallNote:
    """What sign-in did during one call, noted by the code that signs each request."""

    refreshed: bool = False


_CALL: ContextVar[_CallNote | None] = ContextVar("opscp_mcp_call", default=None)


@dataclass(slots=True)
class _SignInState:
    """Shared by every request of one session: once sign-in has failed, it has failed."""

    failure: Exception | None = None


class _PersonAuth(httpx2.Auth):
    def __init__(self, tokens: TokenClient, person_token: str, run_id: str | None) -> None:
        self._tokens = tokens
        self._person_token = person_token
        self._run_id = run_id
        self.state = _SignInState()

    async def _token(self, replacing: str | None = None) -> str:
        try:
            return await self._tokens.token_for(self._person_token, replacing=replacing)
        except (ReauthRequiredError, TokenServiceError) as error:
            self.state.failure = error
            raise

    async def async_auth_flow(
        self, request: httpx2.Request
    ) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        token = await self._token()
        request.headers["Authorization"] = _BEARER + token
        if self._run_id is not None:
            request.headers[RUN_HEADER] = self._run_id
        response = yield request
        if response.status_code != _UNAUTHORISED:
            return
        await response.aread()
        request.headers["Authorization"] = _BEARER + await self._token(replacing=token)
        note = _CALL.get()
        if note is not None:
            note.refreshed = True
        again = yield request
        if again.status_code == _UNAUTHORISED:
            self.state.failure = ReauthRequiredError()


def _error_body(result: CallToolResult) -> ErrorBody:
    for block in result.content:
        if isinstance(block, TextContent):
            try:
                return ErrorBody.model_validate_json(block.text)
            except ValidationError:
                break
    return InternalError().body()


def _rest_error(response: httpx2.Response) -> McpCallError:
    try:
        body = ErrorBody.model_validate_json(response.content)
    except ValidationError:
        body = InternalError().body()
    return McpCallError(response.status_code, body)


class HttpMcpSession:
    def __init__(self, client: Client[Any], http: httpx2.AsyncClient, auth: _PersonAuth) -> None:
        self._unconnected = client
        self._http = http
        self._auth = auth
        self._connecting = asyncio.Lock()
        self._connected = False

    async def _mcp(self) -> Client[Any]:
        """The MCP client, connected on first use: the routes beside it need no handshake."""
        async with self._connecting:
            if not self._connected:
                try:
                    await self._unconnected.__aenter__()
                except _MAY_BE_THE_CONNECTION as error:
                    self.fail(error)
                self._connected = True
        return self._unconnected

    async def close(self) -> None:
        if not self._connected:
            return
        # A transport that failed during the session may fail again as it closes. The first
        # failure is the one to report.
        with suppress(*_MAY_BE_THE_CONNECTION):
            await self._unconnected.close()

    def fail(self, error: Exception) -> NoReturn:
        """Raises why a request failed: sign-in, if sign-in failed; otherwise the server is away.

        Anything that is not the connection failing is a defect and is raised as it is.
        """
        if isinstance(self._auth.state.failure, ReauthRequiredError):
            raise ReauthRequiredError from error
        if not _only_the_connection(error):
            raise error
        _LOG.warning("mcp_unreachable", **failure_site(error))
        raise McpUnavailableError from error

    async def tools(self) -> tuple[ToolSpec, ...]:
        try:
            listed = await (await self._mcp()).list_tools()
        except _MAY_BE_THE_CONNECTION as error:
            self.fail(error)
        return tuple(
            ToolSpec(
                name=tool.name,
                description=tool.description or "",
                input_schema=tool.input_schema,
                read_only=bool(tool.annotations and tool.annotations.read_only_hint),
            )
            for tool in listed
        )

    async def call(self, tool: str, arguments: Json) -> ToolOutcome:
        note = _CallNote()
        marker = _CALL.set(note)
        started = time.monotonic()
        try:
            client = await self._mcp()
            result = await client.call_tool(tool, arguments, raise_on_error=False)
        except _MAY_BE_THE_CONNECTION as error:
            self.fail(error)
        finally:
            _CALL.reset(marker)
        duration_ms = round((time.monotonic() - started) * _MS_PER_S)
        if result.is_error or result.structured_content is None:
            body = _error_body(result)
            denied = body.code == ErrorCode.PERMISSION_DENIED
            return ToolOutcome(
                status="denied" if denied else "error",
                data=None,
                provenance_id=None,
                error=body,
                duration_ms=duration_ms,
                size_bytes=0,
                sign_in_refreshed=note.refreshed,
            )
        content = result.structured_content
        provenance = content.get("provenance")
        return ToolOutcome(
            status="ok",
            data=content.get("data"),
            provenance_id=as_object(provenance).get("id"),
            error=None,
            duration_ms=duration_ms,
            size_bytes=len(json.dumps(content, ensure_ascii=False).encode()),
            sign_in_refreshed=note.refreshed,
        )

    async def resource_versions(self) -> dict[str, str | None]:
        try:
            listed = await (await self._mcp()).list_resources()
        except _MAY_BE_THE_CONNECTION as error:
            self.fail(error)
        versions: dict[str, str | None] = {}
        for resource in listed:
            version = (resource.meta or {}).get(VERSION_KEY)
            versions[str(resource.uri)] = version if isinstance(version, str) else None
        return versions

    async def resource(self, uri: str) -> Reference:
        try:
            contents = await (await self._mcp()).read_resource(uri)
        except _MAY_BE_THE_CONNECTION as error:
            refusal = _refusal_in(error)
            if refusal is not None:
                raise McpCallError(0, refusal) from error
            self.fail(error)
        for content in contents:
            if isinstance(content, TextResourceContents):
                version: object = (content.meta or {}).get(VERSION_KEY)
                return Reference(
                    uri=uri,
                    version=version if isinstance(version, str) else None,
                    text=content.text,
                )
        raise McpCallError(0, InternalError().body())

    async def prompt(self, name: str, arguments: dict[str, str]) -> str:
        try:
            result = await (await self._mcp()).get_prompt(name, arguments)
        except _MAY_BE_THE_CONNECTION as error:
            refusal = _refusal_in(error)
            if refusal is not None:
                raise McpCallError(0, refusal) from error
            self.fail(error)
        return "\n".join(
            message.content.text
            for message in result.messages
            if isinstance(message.content, TextContent)
        )

    async def _rest(self, method: str, path: str, body: Json | None = None) -> Json:
        try:
            response = await self._http.request(method, path, json=body)
        except _MAY_BE_THE_CONNECTION as error:
            self.fail(error)
        if response.status_code == _UNAUTHORISED:
            raise ReauthRequiredError
        if not response.is_success:
            raise _rest_error(response)
        answer: object = response.json()
        held = as_object(answer)
        if not isinstance(answer, dict):
            raise McpCallError(response.status_code, InternalError().body())
        return held

    async def identity(self) -> Json:
        return await self._rest("GET", "/identity")

    async def approvals(self, ticket_id: int) -> Json:
        return await self._rest("GET", f"/approvals?ticket_id={ticket_id}")

    async def approval(self, action_id: UUID) -> Json:
        return await self._rest("GET", f"/approvals/{action_id}")

    async def approve(self, action_id: UUID, args_hash: str) -> Json:
        return await self._rest("POST", f"/approvals/{action_id}/approve", {"args_hash": args_hash})

    async def reject(self, action_id: UUID, reason: str) -> Json:
        return await self._rest("POST", f"/approvals/{action_id}/reject", {"reason": reason})

    async def approval_token(self, action_id: UUID) -> str:
        answer = await self._rest("POST", f"/approvals/{action_id}/token")
        token = answer.get("token")
        if not isinstance(token, str):
            raise McpCallError(0, InternalError().body())
        return token

    async def retry_refund(self, refund_id: int) -> Json:
        return await self._rest("POST", f"/refunds/{refund_id}/retry")


def _refusal_in(error: Exception) -> ErrorBody | None:
    """The server's own refusal of a resource or prompt, which it sends as the message.

    None for any other failure, such as a connection that closed: that is not a refusal,
    and must not be mistaken for "this person may not read it".
    """
    if not isinstance(error, MCPError):
        return None
    try:
        return ErrorBody.model_validate_json(error.error.message)
    except ValidationError:
        return None


class McpGateway:
    """Opens sessions with the MCP server, one per person and piece of work."""

    def __init__(
        self,
        base_url: str,
        tokens: TokenClient,
        *,
        timeout_s: int,
        http_factory: HttpFactory = httpx2.AsyncClient,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._tokens = tokens
        self._timeout_s = timeout_s
        self._http_factory = http_factory

    def session(
        self, person_token: str, run_id: str | None = None
    ) -> AbstractAsyncContextManager[McpSession]:
        """`run_id` names the run to the server, which writes it beside each audit row."""

        @asynccontextmanager
        async def opened() -> AsyncGenerator[McpSession]:
            auth = _PersonAuth(self._tokens, person_token, run_id)
            transport = StreamableHttpTransport(
                self._base_url + MCP_PATH, auth=auth, httpx_client_factory=self._http_factory
            )
            client = Client(transport, timeout=self._timeout_s)
            async with self._http_factory(
                base_url=self._base_url, auth=auth, timeout=httpx2.Timeout(self._timeout_s)
            ) as http:
                session = HttpMcpSession(client, http, auth)
                try:
                    yield session
                finally:
                    await session.close()

        return opened()

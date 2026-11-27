"""What the agent sees of the MCP server, whichever way it is reached."""

from dataclasses import dataclass
from typing import Any, Literal, Protocol
from uuid import UUID

from opscp_common.errors import ErrorBody

type CallStatus = Literal["ok", "denied", "error"]
type Json = dict[str, Any]


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolSpec:
    name: str
    description: str
    input_schema: Json
    read_only: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolOutcome:
    """One tool call as it ended. `data` is the tool's own result, without its wrapper."""

    status: CallStatus
    data: Json | None
    provenance_id: str | None
    error: ErrorBody | None
    duration_ms: int
    size_bytes: int
    sign_in_refreshed: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class Reference:
    uri: str
    version: str | None
    text: str


class McpCallError(Exception):
    """A route beside the MCP endpoint refused. Carries the server's own error body."""

    def __init__(self, status: int, body: ErrorBody) -> None:
        super().__init__(body.code.value)
        self.status = status
        self.body = body


class McpSession(Protocol):
    """The MCP server as one signed-in person reaches it. Every call carries their token.

    Any method may raise ReauthRequiredError when the person must sign in again, and
    McpUnavailableError when the server cannot be reached at all.
    """

    async def tools(self) -> tuple[ToolSpec, ...]: ...

    async def call(self, tool: str, arguments: Json) -> ToolOutcome: ...

    async def resource_versions(self) -> dict[str, str | None]:
        """The resources this person may read, each with the version of its text."""
        ...

    async def resource(self, uri: str) -> Reference: ...

    async def prompt(self, name: str, arguments: dict[str, str]) -> str: ...

    async def identity(self) -> Json: ...

    async def approvals(self, ticket_id: int) -> Json: ...

    async def approval(self, action_id: UUID) -> Json: ...

    async def approve(self, action_id: UUID, args_hash: str) -> Json: ...

    async def reject(self, action_id: UUID, reason: str) -> Json: ...

    async def approval_token(self, action_id: UUID) -> str: ...

    async def retry_refund(self, refund_id: int) -> Json: ...


class McpUnavailableError(Exception):
    """The MCP server did not answer."""

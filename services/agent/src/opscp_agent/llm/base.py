"""What the graph asks of a model, whichever model answers."""

from dataclasses import dataclass, field
from typing import Any, Literal, NotRequired, Protocol, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from opscp_agent.mcp_client.types import ToolSpec

type Purpose = Literal["investigate", "reply"]
type Confidence = Literal["low", "medium", "high"]


class ToolCall(TypedDict):
    id: str
    name: str
    args: dict[str, Any]


class Message(TypedDict):
    role: Literal["user", "assistant", "tool"]
    content: str
    tool_calls: NotRequired[list[ToolCall]]
    tool_call_id: NotRequired[str]


class _Part(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Evidence(_Part):
    """One claim and the tool result it rests on."""

    provenance_id: str = Field(min_length=1)
    claim: str


class ProposedAction(_Part):
    """Why one requested change is justified: the tool results that support it."""

    call_id: str = Field(min_length=1)
    evidence: tuple[str, ...]


class Finding(_Part):
    """What the model concluded. It accompanies the model's last turn of an investigation."""

    root_cause: str
    evidence: tuple[Evidence, ...] = ()
    proposed_actions: tuple[ProposedAction, ...] = ()
    confidence: Confidence = "medium"
    caveats: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Usage:
    tokens_in: int
    tokens_out: int
    tokens_cached: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelTurn:
    """One answer of the model: text for the person, and tool calls or a finding."""

    text: str
    usage: Usage
    tool_calls: tuple[ToolCall, ...] = ()
    finding: Finding | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelRequest:
    purpose: Purpose
    system: str
    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...] = field(default=())


class ModelUnavailableError(Exception):
    """The model did not answer. `retry_after_s` is set when it said how long to wait."""

    def __init__(self, retry_after_s: float | None = None) -> None:
        super().__init__("the model is unavailable")
        self.retry_after_s = retry_after_s


class ChatModel(Protocol):
    @property
    def name(self) -> str:
        """The deployment or model name, as usage and cost are recorded under."""
        ...

    async def complete(self, request: ModelRequest) -> ModelTurn:
        """Raises ModelUnavailableError."""
        ...

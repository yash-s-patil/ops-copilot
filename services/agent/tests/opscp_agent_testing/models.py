"""Models for tests: one that says exactly what a test tells it to, turn by turn."""

import asyncio
from collections.abc import Callable, Sequence
from typing import Any

from opscp_agent.guardrails.spotlight import read_back
from opscp_agent.llm.base import (
    Finding,
    ModelRequest,
    ModelTurn,
    ModelUnavailableError,
    ProposedAction,
    ToolCall,
    Usage,
)

USAGE = Usage(tokens_in=100, tokens_out=10)

type Say = ModelTurn | Callable[[ModelRequest], ModelTurn] | Exception


def reads(*calls: tuple[str, dict[str, Any]], at: int = 1) -> ModelTurn:
    return ModelTurn(
        text="",
        usage=USAGE,
        tool_calls=tuple(
            ToolCall(id=f"read-{at}-{n}", name=name, args=args)
            for n, (name, args) in enumerate(calls, start=1)
        ),
    )


def says(text: str, **finding: Any) -> ModelTurn:
    return ModelTurn(text=text, usage=USAGE, finding=Finding(root_cause=text, **finding))


def sources(request: ModelRequest) -> list[str]:
    """The provenance ids of the tool results the model has been shown, in order."""
    found: list[str] = []
    for message in request.messages:
        body = read_back(message["content"]) if message["role"] == "tool" else None
        if body is not None and "error" not in body and "not_done" not in body:
            found.append(message["content"].split('source="', 1)[1].split('"', 1)[0])
    return found


def asks(
    *calls: tuple[str, dict[str, Any]], at: int = 1, cite: bool = True
) -> Callable[[ModelRequest], ModelTurn]:
    """A turn that asks for changes, citing every result seen so far unless told not to."""

    def turn(request: ModelRequest) -> ModelTurn:
        wanted = tuple(
            ToolCall(id=f"write-{at}-{n}", name=name, args=args)
            for n, (name, args) in enumerate(calls, start=1)
        )
        cited = tuple(sources(request)) if cite else ()
        return ModelTurn(
            text="I suggest these changes.",
            usage=USAGE,
            tool_calls=wanted,
            finding=Finding(
                root_cause="The order is delayed.",
                proposed_actions=tuple(
                    ProposedAction(call_id=call["id"], evidence=cited) for call in wanted
                ),
            ),
        )

    return turn


class Told:
    """Answers with what it was given, one turn at a time, and keeps what it was asked."""

    def __init__(self, turns: Sequence[Say], name: str = "told") -> None:
        self._turns = list(turns)
        self._name = name
        self.requests: list[ModelRequest] = []

    @property
    def name(self) -> str:
        return self._name

    async def complete(self, request: ModelRequest) -> ModelTurn:
        self.requests.append(request)
        if not self._turns:
            raise AssertionError("the model was asked more often than the test expected")
        say = self._turns.pop(0)
        if isinstance(say, Exception):
            raise say
        return say(request) if callable(say) else say


class Away:
    name = "away"

    def __init__(self) -> None:
        self.asked = 0

    async def complete(self, request: ModelRequest) -> ModelTurn:
        self.asked += 1
        raise ModelUnavailableError


class Stalls:
    """Gives the turns it was told to, then never answers: the process dies waiting."""

    name = "stalls"

    def __init__(self, *first: ModelTurn) -> None:
        self._first = list(first)
        self.reached = asyncio.Event()
        self.asked = 0

    async def complete(self, request: ModelRequest) -> ModelTurn:
        self.asked += 1
        if self._first:
            return self._first.pop(0)
        self.reached.set()
        await asyncio.sleep(3600)
        raise AssertionError("a stalled model was left waiting for an hour")

"""What the steps of a run are handed for the length of one invocation. None of it is saved.

The MCP session carries the person's sign-in. It lives here, in memory, and not in the
run's state, so no token is ever written to a checkpoint.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from opscp_agent.auth.verifier import Person
from opscp_agent.events import types as events
from opscp_agent.events.bus import Bus
from opscp_agent.guardrails.budget import Budget
from opscp_agent.guardrails.output import Triggered
from opscp_agent.llm.base import ChatModel, Usage
from opscp_agent.mcp_client.resource_cache import ResourceCache
from opscp_agent.mcp_client.types import McpSession
from opscp_agent.runs.store import ModelCall, Progress, RunAction, RunStore
from opscp_agent.usage.cost import CostEstimate
from opscp_common.limits import Limits


class Journal:
    """Writes what happens in one run: its events, its usage and its guardrail events."""

    def __init__(
        self, store: RunStore, bus: Bus, cost: CostEstimate, run_id: UUID, attempt: int
    ) -> None:
        self._store = store
        self._bus = bus
        self._cost = cost
        self.run_id = run_id
        self.attempt = attempt

    async def emit(self, kind: str, key: str, payload: dict[str, Any]) -> None:
        """An event is stored before anyone is told of it. A key is stored only once."""
        if await self._store.append_event(self.run_id, kind, key, payload) is not None:
            self._bus.publish(str(self.run_id))

    async def model_call(self, node: str, model: str, usage: Usage, latency_ms: int) -> None:
        await self._store.record_model_call(
            self.run_id,
            ModelCall(
                node=node,
                model=model,
                usage=usage,
                latency_ms=latency_ms,
                cost_inr=self._cost.inr(model, usage),
            ),
        )

    async def budget(self, budget: Budget, model: str) -> None:
        await self._store.record_progress(
            self.run_id,
            Progress(
                tool_calls=budget["tool_calls"],
                model_turns=budget["model_turns"],
                elapsed_ms=budget["elapsed_ms"],
            ),
        )
        spent = Usage(
            tokens_in=budget["tokens_in"],
            tokens_out=budget["tokens_out"],
            tokens_cached=budget["tokens_cached"],
        )
        cost = self._cost.inr(model, spent)
        await self.emit(
            events.BUDGET_UPDATED,
            f"budget:{budget['model_turns']}:{budget['tool_calls']}",
            {
                "tokens_in": spent.tokens_in,
                "tokens_out": spent.tokens_out,
                "tokens_cached": spent.tokens_cached,
                "estimated_cost_inr": None if cost is None else str(cost),
                "tool_calls": budget["tool_calls"],
                "model_turns": budget["model_turns"],
                "elapsed_ms": budget["elapsed_ms"],
            },
        )

    async def guardrail(self, triggered: Triggered, key: str) -> dict[str, Any]:
        await self._store.record_guardrail(self.run_id, triggered)
        payload = {
            "layer": triggered.layer,
            "rule": triggered.rule,
            "severity": triggered.severity,
            "action": triggered.action,
            "detail": triggered.detail,
        }
        await self.emit(events.GUARDRAIL_TRIGGERED, f"guardrail:{key}", payload)
        return payload

    async def action(self, action: RunAction) -> None:
        await self._store.add_action(action)

    async def actions(self) -> dict[str, RunAction]:
        """The changes this run asked for, by id, each with the decision noted for it."""
        noted = await self._store.actions_of(self.run_id)
        return {str(action.action_id): action for action in noted}


@dataclass(frozen=True, slots=True, kw_only=True)
class RunContext:
    person: Person
    mcp: McpSession
    model: ChatModel
    journal: Journal
    resources: ResourceCache
    limits: Limits
    canary: str = field(repr=False)
    now: Callable[[], datetime]

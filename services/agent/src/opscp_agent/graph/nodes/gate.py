"""gate: decides where the model's turn goes. A change never goes to the step that reads."""

from dataclasses import dataclass

from langgraph.runtime import Runtime
from langgraph.types import Command

from opscp_agent.graph import names
from opscp_agent.graph.context import RunContext
from opscp_agent.graph.nodes.reason import over_budget
from opscp_agent.graph.state import RunState
from opscp_agent.guardrails.budget import no_room_for
from opscp_agent.llm.base import ToolCall


@dataclass(frozen=True, slots=True)
class Asked:
    """The calls of a turn, by what may be done with them."""

    reads: list[ToolCall]
    writes: list[ToolCall]
    unlisted: list[ToolCall]


def split(state: RunState) -> Asked:
    """The turn's calls that read, those that ask for a change, and those for a tool the
    person was not shown. Only a tool the server listed as read-only is ever treated as
    a read; a tool it did not list is not called at all.
    """
    turn = state["turn"] or {}
    calls: list[ToolCall] = turn.get("tool_calls", [])
    listed = {tool["name"]: tool["read_only"] for tool in state["tools"]}
    return Asked(
        reads=[call for call in calls if listed.get(call["name"]) is True],
        writes=[call for call in calls if listed.get(call["name"]) is False],
        unlisted=[call for call in calls if call["name"] not in listed],
    )


async def gate(state: RunState, runtime: Runtime[RunContext]) -> Command[str]:
    asked = split(state)
    wanted = len(asked.reads) if asked.reads else len(asked.writes)
    refusal = no_room_for(state["budget"], runtime.context.limits.budget, wanted)
    if wanted and refusal is not None:
        return await over_budget(state, runtime.context, refusal)
    if asked.reads or asked.unlisted:
        return Command(goto=names.TOOLS)
    if asked.writes:
        return Command(goto=names.PROPOSE)
    return Command(goto=names.REPLY)

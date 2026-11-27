"""stop: ends a run that may not go on, and says in plain words why."""

from typing import Any

from langgraph.runtime import Runtime

from opscp_agent.events import types as events
from opscp_agent.graph.context import RunContext
from opscp_agent.graph.nodes.reply import result_of
from opscp_agent.graph.state import RunState


async def stop(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    """No further model or tool call is made. What is known so far is returned."""
    result = result_of(state, state["actions"], None, [])
    await runtime.context.journal.emit(
        events.RUN_STOPPED,
        "run.stopped",
        {"reason": state["stop_reason"], "code": state["stop_code"], "known": result},
    )
    return {"result": result, "status": "stopped"}

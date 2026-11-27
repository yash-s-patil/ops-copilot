"""The graph: which step follows which.

Each step after `prepare` names the next one itself, so the only fixed edges are the first
and the two that end a run. A change the model asks for can reach the server only through
`propose`, which holds no approval, and `execute`, which runs only after people decided.
"""

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import (  # pyright: ignore[reportMissingTypeStubs] -- typed inline, in a namespace package
    END,
    START,
    StateGraph,
)
from langgraph.graph.state import (  # pyright: ignore[reportMissingTypeStubs] -- as above
    CompiledStateGraph,
)

from opscp_agent.graph import names
from opscp_agent.graph.context import RunContext
from opscp_agent.graph.nodes.await_approval import await_approval
from opscp_agent.graph.nodes.execute import execute
from opscp_agent.graph.nodes.gate import gate
from opscp_agent.graph.nodes.prepare import prepare
from opscp_agent.graph.nodes.propose import propose
from opscp_agent.graph.nodes.reason import reason
from opscp_agent.graph.nodes.reply import reply
from opscp_agent.graph.nodes.stop import stop
from opscp_agent.graph.nodes.tools import tools
from opscp_agent.graph.state import RunState
from opscp_agent.guardrails.budget import new_budget
from opscp_agent.mcp_client.tool_adapter import no_facts

type RunGraph = CompiledStateGraph[RunState, RunContext, RunState, RunState]


def build_graph(checkpointer: BaseCheckpointSaver[Any]) -> RunGraph:
    # LangGraph leaves parts of its builder untyped, so the builder is held as Any here
    # and nowhere else; what it builds is typed again.
    graph: Any = StateGraph(RunState, context_schema=RunContext)
    graph.add_node(names.PREPARE, prepare)
    graph.add_node(names.REASON, reason, destinations=(names.GATE, names.STOP))
    graph.add_node(
        names.GATE, gate, destinations=(names.TOOLS, names.PROPOSE, names.REPLY, names.STOP)
    )
    graph.add_node(names.TOOLS, tools, destinations=(names.REASON, names.STOP))
    graph.add_node(names.PROPOSE, propose, destinations=(names.AWAIT_APPROVAL, names.REASON))
    graph.add_node(
        names.AWAIT_APPROVAL,
        await_approval,
        destinations=(names.EXECUTE, names.REASON, names.STOP),
    )
    graph.add_node(names.EXECUTE, execute, destinations=(names.REPLY,))
    graph.add_node(names.REPLY, reply)
    graph.add_node(names.STOP, stop)
    graph.add_edge(START, names.PREPARE)
    graph.add_edge(names.PREPARE, names.REASON)
    graph.add_edge(names.REPLY, END)
    graph.add_edge(names.STOP, END)
    compiled: RunGraph = graph.compile(checkpointer=checkpointer)
    return compiled


def first_state(
    *,
    run_id: str,
    user: dict[str, Any],
    message: str,
    ticket_id: int | None,
    order_id: int | None,
) -> RunState:
    return RunState(
        run_id=run_id,
        user=user,
        ticket_id=ticket_id,
        order_id=order_id,
        message=message,
        messages=[],
        resources=[],
        procedure="",
        tools=[],
        turn=None,
        finding=None,
        tool_calls=[],
        facts=no_facts(),
        pending_actions=[],
        actions=[],
        budget=new_budget(),
        rejections=0,
        invalid_calls={},
        caveats=[],
        guardrail_events=[],
        status="running",
        stop_reason=None,
        stop_code=None,
        result=None,
    )

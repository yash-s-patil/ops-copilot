"""prepare: learn what this person may use, and load what every investigation starts from."""

import time
from dataclasses import asdict
from typing import Any

from langgraph.runtime import Runtime

from opscp_agent.events import types as events
from opscp_agent.graph.context import RunContext
from opscp_agent.graph.state import RunState
from opscp_agent.graph.steps import spent
from opscp_agent.guardrails.input import order_named_in
from opscp_agent.llm.base import Message
from opscp_agent.mcp_client.resource_cache import HEALTH_URI
from opscp_agent.mcp_client.types import McpCallError

INVESTIGATE_ORDER = "investigate_order"


async def prepare(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    began = time.monotonic()
    context = runtime.context
    person = context.person
    await context.journal.emit(
        events.RUN_STARTED,
        "run.started",
        {
            "run_id": state["run_id"],
            "user": {"name": person.name, "roles": list(person.roles)},
            "model": context.model.name,
        },
    )
    tools = await context.mcp.tools()
    references = await context.resources.references(context.mcp)
    order_id = state["order_id"] or order_named_in(state["message"])
    procedure = ""
    if order_id is not None:
        asked = {"order_id": str(order_id)} | (
            {} if state["ticket_id"] is None else {"ticket_id": str(state["ticket_id"])}
        )
        try:
            procedure = await context.mcp.prompt(INVESTIGATE_ORDER, asked)
        except McpCallError:
            # The person's role does not include the procedure. The rules still apply.
            procedure = ""
    await context.journal.emit(
        events.CONTEXT_LOADED,
        "context.loaded",
        {
            "resources": [{"uri": held.uri, "version": held.version} for held in references],
            "tools": [tool.name for tool in tools],
        },
    )
    about = (
        ""
        if state["ticket_id"] is None
        else f"\n\nThis is about ticket {state['ticket_id']}, which concerns order {order_id}."
    )
    return {
        "order_id": order_id,
        "tools": [asdict(tool) for tool in tools],
        "resources": [
            {
                "uri": held.uri,
                "version": held.version,
                "text": held.text,
                "changes": held.uri == HEALTH_URI,
            }
            for held in references
        ],
        "procedure": procedure,
        "messages": [Message(role="user", content=state["message"] + about)],
        "budget": spent(state["budget"], began),
    }

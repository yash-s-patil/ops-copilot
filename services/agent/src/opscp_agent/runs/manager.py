"""Runs the graph for each run, and picks a run up again when it can go on.

A run is worked on by one process at a time, which holds a lease on it and renews the
lease while it works. A run whose process has died is taken up by whichever process its
owner next reaches, from the last saved step. The owner must be the one to reach it,
because only their sign-in lets the run read anything.
"""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from opscp_agent.auth.obo import ReauthRequiredError
from opscp_agent.auth.verifier import Caller
from opscp_agent.events import types as events
from opscp_agent.events.bus import Bus
from opscp_agent.graph.build import RunGraph, first_state
from opscp_agent.graph.context import Journal, RunContext
from opscp_agent.guardrails.input import clean_message
from opscp_agent.llm.base import ChatModel, ModelUnavailableError
from opscp_agent.mcp_client.client import McpGateway
from opscp_agent.mcp_client.resource_cache import ResourceCache
from opscp_agent.mcp_client.tool_adapter import GET_TICKET
from opscp_agent.mcp_client.types import McpCallError, McpSession, McpUnavailableError
from opscp_agent.runs.store import (
    AWAITING_APPROVAL,
    CANCELLED,
    RESUMABLE,
    NewRun,
    Outcome,
    Run,
    RunStatus,
    RunStore,
)
from opscp_agent.usage.cost import CostEstimate
from opscp_common.errors import InternalError
from opscp_common.failures import failure_site
from opscp_common.limits import Limits

INTERRUPTED = "__interrupt__"
MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
INTERNAL = "INTERNAL"
REAUTH_REQUIRED = "reauth_required"
_FAILURES = {
    MODEL_UNAVAILABLE: "The assistant is unavailable right now. Try again in a few minutes.",
    BACKEND_UNAVAILABLE: (
        "Meridian's systems could not be reached, so the investigation could not go on."
    ),
    INTERNAL: "Something went wrong, so the investigation could not go on.",
}
_NEW: tuple[RunStatus, ...] = ("queued",)
_LOG = structlog.get_logger(__name__)


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True, kw_only=True)
class Workings:
    """What every run is given. Built once at start-up."""

    graph: RunGraph
    store: RunStore
    bus: Bus
    gateway: McpGateway
    model: ChatModel
    cost: CostEstimate
    resources: ResourceCache
    limits: Limits
    canary: str = field(repr=False)
    storage_ready: Callable[[], Awaitable[None]]
    now: Callable[[], datetime] = utc_now


def thread_of(run_id: UUID, steps_max: int) -> RunnableConfig:
    """Only the run's id is given as configuration, since configuration is saved."""
    return RunnableConfig(configurable={"thread_id": str(run_id)}, recursion_limit=steps_max)


class RunManager:
    def __init__(self, workings: Workings) -> None:
        self._w = workings
        self._owner = uuid4()
        self._working: dict[UUID, asyncio.Task[None]] = {}
        self._following: set[asyncio.Task[None]] = set()

    async def start(self, caller: Caller, message: str, ticket_id: int | None) -> UUID:
        """Records a new run and begins it. Raises what the ticket's own read raises."""
        cleaned = clean_message(message, self._w.limits.user.message_chars)
        # Before anything is recorded: a run that cannot be saved must not be left queued.
        await self._w.storage_ready()
        order_id: int | None = None
        if ticket_id is not None:
            async with self._w.gateway.session(caller.token) as mcp:
                order_id = await _order_of(mcp, ticket_id)
        run_id = uuid4()
        await self._w.store.create(
            NewRun(
                id=run_id,
                person=caller.person,
                ticket_id=ticket_id,
                order_id=order_id,
                message=cleaned,
                model=self._w.model.name,
            )
        )
        await self._take(run_id, caller, _NEW)
        return run_id

    async def nudge(self, run: Run, caller: Caller) -> None:
        """Called when the run's owner looks at it: lets it go on if it can and nobody has it."""
        if run.person.oid != caller.person.oid:
            return
        if run.status in RESUMABLE:
            await self._take(run.id, caller, RESUMABLE)
        elif run.status == AWAITING_APPROVAL:
            await self.resume_if_decided(run.id, caller)

    async def resume_if_decided(
        self, run_id: UUID, caller: Caller, *, ask_server: bool = False
    ) -> bool:
        """Lets a waiting run go on once every change it asked for has been decided.

        With `ask_server`, a change with no decision noted is looked up on the MCP server
        first. That is for the requester's own click: a note can be lost, or a decision
        made where it did not pass through the agent, and the run must not wait for ever.
        """
        run = await self._w.store.get(run_id)
        if run is None or run.status != AWAITING_APPROVAL:
            return False
        if ask_server:
            await self._learn_decisions(run_id, caller)
        if not await self._all_decided(run_id):
            return False
        return await self._take(run_id, caller, (AWAITING_APPROVAL,))

    async def _learn_decisions(self, run_id: UUID, caller: Caller) -> None:
        now = self._w.now()
        undecided = [a for a in await self._w.store.actions_of(run_id) if a.is_open(now)]
        if not undecided:
            return
        async with self._w.gateway.session(caller.token, str(run_id)) as mcp:
            for action in undecided:
                view = await mcp.approval(action.action_id)
                status = view.get("status")
                if status in ("approved", "rejected"):
                    await self._w.store.note_decision(
                        action.action_id,
                        status,
                        str(view.get("approver_name") or "someone"),
                        view.get("decision_reason"),
                    )

    async def _all_decided(self, run_id: UUID) -> bool:
        """Whether nothing the run asked for is still open, by the agent's own notes.

        The server is not asked: this is looked at each time the run's owner looks at the
        run, and their look-ups are counted against them there.
        """
        now = self._w.now()
        return not any(action.is_open(now) for action in await self._w.store.actions_of(run_id))

    async def cancel(self, run: Run, caller: Caller) -> bool:
        if not await self._w.store.cancel(run.id, caller.person.oid):
            return False
        journal = Journal(self._w.store, self._w.bus, self._w.cost, run.id, 0)
        await journal.emit(
            events.RUN_STOPPED,
            "run.stopped",
            {"reason": "You stopped this investigation.", "code": CANCELLED, "known": None},
        )
        working = self._working.get(run.id)
        if working is not None:
            working.cancel()
        await self._withdraw(run.id, caller)
        return True

    async def _withdraw(self, run_id: UUID, caller: Caller) -> None:
        """What a cancelled run asked for and nobody decided is taken back.

        If that cannot be done the run is stopped all the same. A change of a stopped run
        is never carried out by the agent, whatever is decided about it later.
        """
        now = self._w.now()
        undecided = [a for a in await self._w.store.actions_of(run_id) if a.is_open(now)]
        if not undecided:
            return
        try:
            async with self._w.gateway.session(caller.token, str(run_id)) as mcp:
                for action in undecided:
                    # Refused when it was decided meanwhile, which needs nothing more.
                    with suppress(McpCallError):
                        await mcp.reject(action.action_id, "The investigation was stopped.")
        except (McpUnavailableError, ReauthRequiredError) as error:
            _LOG.warning("not_withdrawn", run_id=str(run_id), error=type(error).__name__)

    def _thread(self, run_id: UUID) -> RunnableConfig:
        return thread_of(run_id, self._w.limits.runs.graph_steps_max)

    async def saved_state(self, run_id: UUID) -> dict[str, Any]:
        return (await self._w.graph.aget_state(self._thread(run_id))).values

    async def idle(self) -> None:
        """Returns once the work now in hand has been put down, however it ended."""
        await asyncio.sleep(0)
        while self._working or self._following:
            in_hand = [*self._working.values(), *self._following]
            await asyncio.gather(*in_hand, return_exceptions=True)
            await asyncio.sleep(0)

    async def close(self) -> None:
        """At shutdown. Runs left unfinished keep their saved steps and can be picked up."""
        working = [*self._following, *self._working.values()]
        for task in working:
            task.cancel()
        await asyncio.gather(*working, return_exceptions=True)

    async def _take(self, run_id: UUID, caller: Caller, held: tuple[RunStatus, ...]) -> bool:
        await self._w.storage_ready()
        attempt = await self._w.store.claim(run_id, self._owner, self._w.limits.runs.lease_s, held)
        if attempt is None:
            return False
        earlier = self._working.get(run_id)
        if earlier is not None and not earlier.done():
            # The store gave the run to this piece of work, so the earlier one has lost it.
            earlier.cancel()
        task = asyncio.create_task(self._work(run_id, caller, attempt))
        self._working[run_id] = task

        def put_down(done: asyncio.Task[None]) -> None:
            # Only its own entry: a later piece of work on the same run may hold it now.
            if self._working.get(run_id) is done:
                del self._working[run_id]

        task.add_done_callback(put_down)
        return True

    async def _work(self, run_id: UUID, caller: Caller, attempt: int) -> None:
        journal = Journal(self._w.store, self._w.bus, self._w.cost, run_id, attempt)
        beating = asyncio.create_task(self._keep_lease(run_id, asyncio.current_task()))
        try:
            outcome = await self._outcome(run_id, caller, journal)
        except asyncio.CancelledError:
            # Stopped by its owner, taken by another process, or the process is closing.
            # What the store says of the run is already right, or its lease will lapse.
            raise
        finally:
            beating.cancel()
        if not await self._w.store.settle(run_id, self._owner, outcome):
            return
        if outcome.status == AWAITING_APPROVAL and await self._all_decided(run_id):
            # A decision arrived while this step was being saved. The run is taken again
            # in a task of its own, once this one has been put down.
            followed = asyncio.create_task(self._follow(run_id, caller, asyncio.current_task()))
            self._following.add(followed)
            followed.add_done_callback(self._following.discard)

    async def _follow(
        self, run_id: UUID, caller: Caller, before: asyncio.Task[None] | None
    ) -> None:
        if before is not None:
            await asyncio.wait([before])
        await self.resume_if_decided(run_id, caller)

    async def _outcome(self, run_id: UUID, caller: Caller, journal: Journal) -> Outcome:
        try:
            final = await self._invoke(run_id, caller, journal)
        except ReauthRequiredError:
            await journal.emit(
                events.RUN_PAUSED,
                f"run.paused:{journal.attempt}",
                {"code": REAUTH_REQUIRED, "reason": "Sign in again to continue."},
            )
            return Outcome(status="reauth_required")
        except ModelUnavailableError:
            return await self._failed(journal, MODEL_UNAVAILABLE)
        except McpUnavailableError:
            return await self._failed(journal, BACKEND_UNAVAILABLE)
        except McpCallError as refused:
            _LOG.warning("run_refused", run_id=str(run_id), code=refused.body.code.value)
            return await self._failed(journal, INTERNAL)
        except Exception as error:  # noqa: BLE001 -- whatever went wrong, the run is ended and its owner told
            _LOG.error("run_failed", run_id=str(run_id), **failure_site(error))
            return await self._failed(journal, INTERNAL)
        if INTERRUPTED in final:
            return Outcome(status="awaiting_approval")
        status = "stopped" if final.get("status") == "stopped" else "completed"
        return Outcome(
            status=status, result=final.get("result"), stop_reason=final.get("stop_reason")
        )

    async def _invoke(self, run_id: UUID, caller: Caller, journal: Journal) -> dict[str, Any]:
        config = self._thread(run_id)
        async with self._w.gateway.session(caller.token, str(run_id)) as mcp:
            context = RunContext(
                person=caller.person,
                mcp=mcp,
                model=self._w.model,
                journal=journal,
                resources=self._w.resources,
                limits=self._w.limits,
                canary=self._w.canary,
                now=self._w.now,
            )
            saved = await self._w.graph.aget_state(config)
            if not saved.values:
                asked: Any = await self._first(run_id)
            elif saved.interrupts:
                if not await self._all_decided(run_id):
                    return {INTERRUPTED: saved.interrupts}
                asked = Command(resume=True)
            else:
                asked = None
            final: dict[str, Any] = await self._w.graph.ainvoke(  # pyright: ignore[reportUnknownMemberType] -- LangGraph leaves this method partly untyped
                asked, config, context=context
            )
            return final

    async def _first(self, run_id: UUID) -> dict[str, Any]:
        run = await self._w.store.get(run_id)
        if run is None:
            raise InternalError
        return dict(
            first_state(
                run_id=str(run.id),
                user={
                    "oid": str(run.person.oid),
                    "name": run.person.name,
                    "roles": list(run.person.roles),
                    "region": run.person.region,
                },
                message=run.message,
                ticket_id=run.ticket_id,
                order_id=run.order_id,
            )
        )

    async def _failed(self, journal: Journal, code: str) -> Outcome:
        message = _FAILURES[code]
        await journal.emit(events.RUN_FAILED, "run.failed", {"code": code, "message": message})
        return Outcome(status="failed", stop_reason=message)

    async def _keep_lease(self, run_id: UUID, working: asyncio.Task[None] | None) -> None:
        """Renews the lease. When it is gone the work is stopped: the run is another's now."""
        limits = self._w.limits.runs
        while True:
            await asyncio.sleep(limits.heartbeat_s)
            try:
                held = await self._w.store.heartbeat(run_id, self._owner, limits.lease_s)
            except Exception as error:  # noqa: BLE001 -- one failed beat must not end the beating
                # The lease outlives two beats, so the next one may still be in time.
                _LOG.warning("heartbeat_failed", run_id=str(run_id), **failure_site(error))
                continue
            if not held:
                if working is not None:
                    working.cancel()
                return


async def _order_of(mcp: McpSession, ticket_id: int) -> int | None:
    """The order a ticket is about, read as the person. Raises when they may not see it."""
    outcome = await mcp.call(GET_TICKET, {"ticket_id": ticket_id, "notes": 0})
    if outcome.error is not None or outcome.data is None:
        raise McpCallError(0, outcome.error or InternalError().body())
    order_id = outcome.data.get("order_id")
    return order_id if isinstance(order_id, int) else None

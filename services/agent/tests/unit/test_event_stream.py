"""The event stream a browser follows, and the one an administrator follows."""

import asyncio
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

from opscp_agent.events import types as events
from opscp_agent.events.admin_projection import admin_view
from opscp_agent.events.bus import Bus
from opscp_agent.events.sse import KEEPALIVE, EventStream, never_gone
from opscp_agent_testing.desk import WAIT_S, Desk, bearer, desk
from opscp_agent_testing.reference import FIRST_HALF, FRESH_ORDER, TICKET
from opscp_common.limits import Limits
from opscp_mcp_testing.identities import PRIYA

ASK = f"What is wrong with order {FRESH_ORDER}?"


def _frames(text: str) -> list[dict[str, str]]:
    frames: list[dict[str, str]] = []
    for block in text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line)
        if fields:
            frames.append(fields)
    return frames


async def _stream(at: Desk, path: str, who: Any, **headers: str) -> list[dict[str, str]]:
    response = await at.agent.get(path, headers=bearer(who) | headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    return _frames(response.text)


async def _taken(stream: AsyncGenerator[str], until: str) -> list[str]:
    """Reads frames until one of the named kind has been read, then closes the stream."""
    frames: list[str] = []
    async with asyncio.timeout(WAIT_S):
        async for frame in stream:
            frames.append(frame)
            if f"event: {until}\n" in frame:
                break
    await stream.aclose()
    return frames


async def test_a_finished_run_is_replayed_in_order_and_the_stream_then_ends(
    limits: Limits,
) -> None:
    async with desk(limits, FIRST_HALF) as at:
        run_id = await at.start(PRIYA, ASK)
        await at.settled(run_id, "completed")

        frames = await _stream(at, f"/api/runs/{run_id}/events", PRIYA)

        assert [frame["event"] for frame in frames] == at.store.kinds(run_id)
        assert [int(frame["id"]) for frame in frames] == list(range(1, len(frames) + 1))
        assert frames[-1]["event"] == events.RUN_COMPLETED


async def test_a_reconnecting_browser_is_sent_only_what_it_has_not_seen(limits: Limits) -> None:
    async with desk(limits, FIRST_HALF) as at:
        run_id = await at.start(PRIYA, ASK)
        await at.settled(run_id, "completed")
        total = len(at.store.events[run_id])

        frames = await _stream(
            at, f"/api/runs/{run_id}/events", PRIYA, **{"Last-Event-ID": str(total - 2)}
        )

        assert [int(frame["id"]) for frame in frames] == [total - 1, total]
        bad = await at.agent.get(
            f"/api/runs/{run_id}/events", headers=bearer(PRIYA) | {"Last-Event-ID": "-1"}
        )
        assert bad.status_code == 422


async def test_a_stream_opened_while_a_run_waits_carries_it_to_its_end(limits: Limits) -> None:
    async with desk(limits, FIRST_HALF) as at:
        run_id = await at.start(PRIYA, "Investigate.", TICKET)
        await at.settled(run_id, "awaiting_approval")
        streams = EventStream(at.store, Bus(), limits.stream.model_copy(update={"poll_ms": 1}))

        waiting = await _taken(streams.of_run(run_id, 0, never_gone), events.APPROVAL_REQUIRED)
        following = streams.of_run(run_id, len(waiting), never_gone)
        await at.decide(PRIYA, run_id)
        async with asyncio.timeout(WAIT_S):
            rest = [frame async for frame in following]

        assert rest[0].startswith(f"id: {len(waiting) + 1}\n")
        assert f"event: {events.RUN_COMPLETED}\n" in rest[-1]


async def test_a_stream_ends_when_its_reader_has_gone(limits: Limits) -> None:
    async with desk(limits, FIRST_HALF) as at:
        run_id = await at.start(PRIYA, "Investigate.", TICKET)
        await at.settled(run_id, "awaiting_approval")
        looks = iter([False, False, True])

        async def gone() -> bool:
            return next(looks)

        streams = EventStream(at.store, Bus(), limits.stream.model_copy(update={"poll_ms": 1}))
        seen = len(at.store.events[run_id])

        assert [frame async for frame in streams.of_run(run_id, seen, gone)] == []


async def test_a_quiet_stream_sends_a_comment_to_keep_the_connection_open(limits: Limits) -> None:
    async with desk(limits, FIRST_HALF) as at:
        run_id = await at.start(PRIYA, "Investigate.", TICKET)
        await at.settled(run_id, "awaiting_approval")
        quick = limits.stream.model_copy(update={"poll_ms": 1, "keepalive_s": 1})
        stream = EventStream(at.store, Bus(), quick).of_run(
            run_id, len(at.store.events[run_id]), never_gone
        )

        async with asyncio.timeout(WAIT_S):
            first = await anext(stream)
        await stream.aclose()

        assert first == KEEPALIVE


async def test_the_live_trace_follows_the_newest_run_in_the_admin_projection(
    limits: Limits,
) -> None:
    async with desk(limits, FIRST_HALF) as at:
        streams = EventStream(at.store, Bus(), limits.stream.model_copy(update={"poll_ms": 1}))
        stream = streams.of_latest_run(never_gone, admin_view)
        first = await at.start(PRIYA, ASK)
        await at.settled(first, "completed")
        frames: list[str] = []
        second: UUID | None = None
        async with asyncio.timeout(WAIT_S):
            async for frame in stream:
                frames.append(frame)
                if f"event: {events.RUN_COMPLETED}\n" not in frame:
                    continue
                if second is not None:
                    break
                second = await at.start(PRIYA, ASK)
                await at.settled(second, "completed")
        await stream.aclose()

        moved = [frame for frame in frames if frame.startswith("event: run\n")]
        assert moved == [
            f'event: run\ndata: {{"run_id": "{first}"}}\n\n',
            f'event: run\ndata: {{"run_id": "{second}"}}\n\n',
        ]
        text = "".join(frames)
        for hidden in ("Ananya", "What is wrong", "Packed for"):
            assert hidden not in text
        assert '"tool": "get_order"' in text


async def test_the_live_trace_is_for_the_administrator_only(limits: Limits) -> None:
    async with desk(limits, FIRST_HALF) as at:
        refused = await at.agent.get("/api/admin/runs/latest/events", headers=bearer(PRIYA))

        assert refused.status_code == 403

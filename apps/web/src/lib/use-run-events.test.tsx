// @vitest-environment jsdom
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { useRunEvents } from "@/lib/use-run-events";
import { FakeEventSource, fakeEventSource } from "@/test/event-source";
import { answerJson, answerText, fakeFetch, sent, type FetchMock } from "@/test/fetch";
import { RUN_ID } from "@/test/fixtures";

const RECONNECT_MAX_MS = 15_000;
const OTHER_RUN = "3d3d3d3d-3d3d-4d3d-8d3d-3d3d3d3d3d3d";

let source: () => FakeEventSource;
let fetchMock: FetchMock;

beforeEach(() => {
  source = fakeEventSource();
  fetchMock = fakeFetch();
  answerJson(fetchMock, {}, { status: 503 });
});

function follow(runId: string | null = RUN_ID) {
  return renderHook(({ id }) => useRunEvents(id, RECONNECT_MAX_MS), {
    initialProps: { id: runId },
  });
}

describe("following a run", () => {
  it("opens no stream while there is no run", () => {
    const { result } = follow(null);
    expect(FakeEventSource.opened).toEqual([]);
    expect(result.current).toEqual({ events: [], connection: "connecting" });
  });

  it("opens the run's stream on this application's own server and listens for every event", () => {
    follow();
    expect(source().url).toBe(`/api/bff/runs/${RUN_ID}/events`);
    for (const type of ["run.started", "tool.finished", "approval.resolved", "run.failed"]) {
      expect(source().listensFor(type)).toBe(true);
    }
  });

  it("is connecting, then open", () => {
    const { result } = follow();
    expect(result.current.connection).toBe("connecting");
    act(() => source().onopen?.());
    expect(result.current.connection).toBe("open");
  });

  it("gathers events in the order they arrive, each with its number, type and payload", () => {
    const { result } = follow();
    act(() => {
      source().send(1, "run.started");
      source().send(2, "tool.started", { call_id: "c1" });
      source().send(3, "tool.finished", { call_id: "c1" });
    });
    expect(result.current.events).toEqual([
      { seq: 1, type: "run.started", data: {} },
      { seq: 2, type: "tool.started", data: { call_id: "c1" } },
      { seq: 3, type: "tool.finished", data: { call_id: "c1" } },
    ]);
    expect(result.current.connection).toBe("open");
  });
});

describe("events that repeat or cannot be read", () => {
  it("ignores an event numbered at or below the last one it holds", () => {
    const { result } = follow();
    act(() => {
      source().send(1, "run.started");
      source().send(2, "tool.started");
      source().send(2, "tool.started");
      source().send(1, "run.started");
      source().send(3, "tool.finished");
    });
    expect(result.current.events.map((event) => event.seq)).toEqual([1, 2, 3]);
  });

  it("keeps what it holds and takes only what is new when the stream is replayed", () => {
    const { result } = follow();
    act(() => {
      source().send(1, "run.started");
      source().send(2, "tool.started");
      source().onerror?.();
      source().send(1, "run.started");
      source().send(2, "tool.started");
      source().send(3, "tool.finished");
    });
    expect(result.current.events.map((event) => event.seq)).toEqual([1, 2, 3]);
    expect(result.current.connection).toBe("open");
  });

  it.each([
    ["no number", "", "{}"],
    ["a number that is not whole", "1.5", "{}"],
    ["a number that is not a number", "abc", "{}"],
    ["a payload that is not JSON", "1", "{not json"],
    ["an empty payload", "1", ""],
  ])("drops an event with %s", (_what, id, data) => {
    const { result } = follow();
    act(() => source().sendRaw(id, "tool.started", data));
    expect(result.current.events).toEqual([]);
  });
});

describe("the end of a run", () => {
  it.each(["run.completed", "run.stopped", "run.failed"])(
    "closes the stream on %s and keeps that event",
    (type) => {
      const { result } = follow();
      act(() => {
        source().send(1, "run.started");
        source().send(2, type);
      });
      expect(source().closed).toBe(true);
      expect(result.current.connection).toBe("ended");
      expect(result.current.events.at(-1)).toMatchObject({ seq: 2, type });
    },
  );

  it("stays open through a pause, which is not an end", () => {
    const { result } = follow();
    act(() => source().send(1, "run.paused", { code: "reauth_required" }));
    expect(source().closed).toBe(false);
    expect(result.current.connection).toBe("open");
  });

  it("closes the stream when the page stops following", () => {
    const { unmount } = follow();
    const opened = source();
    unmount();
    expect(opened.closed).toBe(true);
  });

  it("closes one run's stream and opens the next when the run changes", () => {
    const { rerender } = follow();
    const first = source();
    rerender({ id: OTHER_RUN });
    expect(first.closed).toBe(true);
    expect(source().url).toContain(OTHER_RUN);
  });
});

describe("a stream that fails", () => {
  it("is lost, and the run is asked for once to learn why", () => {
    const { result } = follow();
    act(() => source().onerror?.());
    expect(result.current.connection).toBe("lost");
    expect(sent(fetchMock).url).toBe(`/api/bff/runs/${RUN_ID}`);
    expect(source().closed).toBe(false);
  });

  it("stays lost, and left to reconnect, when the server is only unreachable", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const { result } = follow();
    act(() => source().onerror?.());
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(result.current.connection).toBe("lost");
    expect(source().closed).toBe(false);
  });

  it("is signed out, and closed for good, when the run is refused with a 401", async () => {
    answerText(fetchMock, "", { status: 401 });
    const { result } = follow();
    act(() => source().onerror?.());
    await waitFor(() => expect(result.current.connection).toBe("signed_out"));
    expect(source().closed).toBe(true);
  });

  it("is open again when an event arrives after the loss", () => {
    const { result } = follow();
    act(() => {
      source().onerror?.();
      source().send(1, "run.started");
    });
    expect(result.current.connection).toBe("open");
  });
});

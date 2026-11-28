// @vitest-environment jsdom
import { QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it } from "vitest";
import { myRunsKey, runFinished, useMyRuns, useTicketsAwaitingMe } from "@/lib/use-my-runs";
import { answerJson, fakeFetch, sent, type FetchMock } from "@/test/fetch";
import { RUN_ID, TICKET_ID } from "@/test/fixtures";
import { newClient } from "@/test/render";

let fetchMock: FetchMock;

beforeEach(() => {
  fetchMock = fakeFetch();
});

function run(status: string, ticketId: number | null) {
  return {
    run_id: RUN_ID,
    ticket_id: ticketId,
    order_id: null,
    status,
    message: "Why is this late?",
    started_at: "2026-11-28T09:00:00Z",
    finished_at: null,
    result: null,
    stop_reason: null,
  };
}

function withClient() {
  const client = newClient();
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, wrapper };
}

async function awaitingAfterLoad(): Promise<number[]> {
  const { client, wrapper } = withClient();
  const { result } = renderHook(() => useTicketsAwaitingMe(), { wrapper });
  await waitFor(() => expect(client.getQueryState(myRunsKey)?.status).not.toBe("pending"));
  return [...result.current].sort();
}

describe("useMyRuns", () => {
  it("asks for the person's own runs, all of them, and keeps them under one key", async () => {
    answerJson(fetchMock, { items: [run("running", TICKET_ID)] });
    const { client, wrapper } = withClient();
    const { result } = renderHook(() => useMyRuns(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(sent(fetchMock).url).toBe("/api/bff/runs");
    expect(result.current.data?.items).toHaveLength(1);
    expect(client.getQueryData(myRunsKey)).toEqual(result.current.data);
  });
});

describe("useTicketsAwaitingMe", () => {
  it("is empty before the runs have loaded", () => {
    answerJson(fetchMock, { items: [] });
    const { wrapper } = withClient();
    const { result } = renderHook(() => useTicketsAwaitingMe(), { wrapper });
    expect(result.current.size).toBe(0);
  });

  it("is empty when the person has no runs", async () => {
    answerJson(fetchMock, { items: [] });
    expect(await awaitingAfterLoad()).toEqual([]);
  });

  it("holds the ticket of a run that waits for the person's approval", async () => {
    answerJson(fetchMock, { items: [run("awaiting_approval", TICKET_ID)] });
    expect(await awaitingAfterLoad()).toEqual([TICKET_ID]);
  });

  it("holds each ticket once, and only those whose run is waiting", async () => {
    const items = [
      run("awaiting_approval", TICKET_ID),
      run("awaiting_approval", TICKET_ID),
      run("running", 2208),
      run("completed", 2209),
      run("awaiting_approval", 2210),
    ];
    answerJson(fetchMock, { items });
    expect(await awaitingAfterLoad()).toEqual([TICKET_ID, 2210]);
  });

  it("leaves out a waiting run that is not about a ticket", async () => {
    answerJson(fetchMock, { items: [run("awaiting_approval", null)] });
    expect(await awaitingAfterLoad()).toEqual([]);
  });

  it("is empty when the runs cannot be read", async () => {
    answerJson(fetchMock, { code: "BACKEND_UNAVAILABLE", message: "Down." }, { status: 503 });
    expect(await awaitingAfterLoad()).toEqual([]);
  });

  it("is empty when the answer is not a list of runs", async () => {
    answerJson(fetchMock, { items: [{ run_id: 7 }] });
    expect(await awaitingAfterLoad()).toEqual([]);
  });
});

describe("runFinished", () => {
  it.each(["completed", "stopped", "failed", "cancelled"])("is true for %s", (status) => {
    expect(runFinished(status)).toBe(true);
  });

  it.each(["running", "awaiting_approval", "paused", "queued", "", "COMPLETED"])(
    "is false for %j",
    (status) => {
      expect(runFinished(status)).toBe(false);
    },
  );
});

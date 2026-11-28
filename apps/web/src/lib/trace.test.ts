import { describe, expect, it } from "vitest";
import type { AdminEvent } from "@/lib/schemas";
import { budgetOf, rawOf, spansOf, timelineOf } from "@/lib/trace";

const START = Date.parse("2026-11-28T09:00:00Z");
const HUMAN_WAIT_MS = 60_000;

type Draft = readonly [afterMs: number, type: string, payload?: Record<string, unknown>];

/** Events numbered from one, each at a number of milliseconds after the run began. */
function events(drafts: readonly Draft[]): AdminEvent[] {
  return drafts.map(([afterMs, type, payload = {}], index) => ({
    seq: index + 1,
    type,
    at: new Date(START + afterMs).toISOString(),
    payload,
  }));
}

const tool = (name: string, over: Record<string, unknown> = {}) => ({
  tool: name,
  phase: "read",
  status: "ok",
  duration_ms: 400,
  bytes: 512,
  ...over,
});

describe("spansOf", () => {
  it("has no bars and a total of one for a run with no events, so nothing divides by zero", () => {
    expect(spansOf([])).toEqual({ spans: [], totalMs: 1 });
  });

  it("places a bar by when its call finished and how long it took", () => {
    const { spans, totalMs } = spansOf(
      events([
        [0, "run.started"],
        [1_000, "tool.finished", tool("get_order")],
      ]),
    );
    expect(spans).toEqual([{ key: "2", name: "get_order", startMs: 600, durationMs: 400 }]);
    expect(totalMs).toBe(1_000);
  });

  it("leaves the wait for a person's decision out of the timeline", () => {
    const { spans, totalMs } = spansOf(
      events([
        [0, "run.started"],
        [1_000, "tool.finished", tool("issue_refund", { phase: "propose" })],
        [2_000, "approval.required"],
        [2_000 + HUMAN_WAIT_MS, "approval.resolved", { decision: "approved" }],
        [3_000 + HUMAN_WAIT_MS, "tool.finished", tool("issue_refund", { phase: "execute" })],
      ]),
    );
    expect(spans.map((span) => [span.startMs, span.durationMs])).toEqual([
      [600, 400],
      [2_600, 400],
    ]);
    expect(totalMs).toBe(3_000);
  });

  it("leaves out every wait when a run was decided more than once", () => {
    const { totalMs } = spansOf(
      events([
        [0, "run.started"],
        [1_000, "approval.required"],
        [1_000 + HUMAN_WAIT_MS, "approval.resolved"],
        [2_000 + HUMAN_WAIT_MS, "approval.required"],
        [2_000 + 2 * HUMAN_WAIT_MS, "approval.resolved"],
        [3_000 + 2 * HUMAN_WAIT_MS, "run.completed"],
      ]),
    );
    expect(totalMs).toBe(3_000);
  });
});

describe("spansOf with events that say little", () => {
  it("does not start a bar before the run began when a call says it took longer", () => {
    const { spans } = spansOf(events([[100, "tool.finished", tool("get_order")]]));
    expect(spans[0]).toMatchObject({ startMs: 0, durationMs: 400 });
  });

  it("draws a bar of no length and no name for a call that reported neither", () => {
    const { spans } = spansOf(
      events([
        [0, "run.started"],
        [500, "tool.finished", {}],
      ]),
    );
    expect(spans).toEqual([{ key: "2", name: "", startMs: 500, durationMs: 0 }]);
  });

  it("copes with a decision as the very first event", () => {
    expect(spansOf(events([[0, "approval.resolved"]]))).toEqual({ spans: [], totalMs: 1 });
  });
});

describe("budgetOf", () => {
  const budget = (calls: number, cost: unknown) => ({
    tokens_in: 1_200,
    tokens_out: 300,
    tool_calls: calls,
    elapsed_ms: 4_100,
    estimated_cost_inr: cost,
  });

  it("is null when the run reported none", () => {
    expect(budgetOf([])).toBeNull();
    expect(budgetOf(events([[0, "run.started"]]))).toBeNull();
  });

  it("is the last one reported, with tokens in and out added together", () => {
    const reported = events([
      [0, "budget.updated", budget(1, "0.10")],
      [10, "tool.finished", tool("get_order")],
      [20, "budget.updated", budget(2, "0.42")],
    ]);
    expect(budgetOf(reported)).toEqual({
      tokens: 1_500,
      toolCalls: 2,
      elapsedMs: 4_100,
      cost: "0.42",
    });
  });

  it("has no cost when none was estimated, and zeroes for what is missing", () => {
    expect(budgetOf(events([[0, "budget.updated", { estimated_cost_inr: null }]]))).toEqual({
      tokens: 0,
      toolCalls: 0,
      elapsedMs: 0,
      cost: null,
    });
  });

  it("does not take a number for a cost", () => {
    expect(budgetOf(events([[0, "budget.updated", budget(1, 0.42)]]))?.cost).toBeNull();
  });
});

const rowsOf = (drafts: readonly Draft[]) => timelineOf(events(drafts));
const rowOf = (type: string, payload: Record<string, unknown> = {}) =>
  rowsOf([[0, type, payload]])[0];

describe("timelineOf", () => {
  it("has no rows for no events, and none for events it does not draw", () => {
    expect(timelineOf([])).toEqual([]);
    expect(
      rowsOf([
        [0, "run.started"],
        [1, "tool.started"],
        [2, "budget.updated"],
      ]),
    ).toEqual([]);
  });

  it("keys each row by the number of its event", () => {
    const rows = rowsOf([
      [0, "run.started"],
      [1, "run.completed"],
    ]);
    expect(rows.map((row) => row.key)).toEqual(["2"]);
  });

  it.each([
    ["read", "ok", "ok"],
    ["propose", "approval required", "warn"],
    ["execute", "executed", "ok"],
  ])("shows a good %s call as %s", (phase, state, tone) => {
    expect(rowOf("tool.finished", tool("issue_refund", { phase }))).toMatchObject({
      label: "issue_refund",
      mono: true,
      state,
      tone,
      aside: "400 ms · 512 B",
    });
  });

  it("shows the error code of a call that went wrong, or its status when it gave none", () => {
    const denied = tool("issue_refund", { status: "denied", error_code: "PERMISSION_DENIED" });
    expect(rowOf("tool.finished", denied)).toMatchObject({
      state: "PERMISSION_DENIED",
      tone: "bad",
    });
    expect(rowOf("tool.finished", tool("get_order", { status: "error" }))).toMatchObject({
      state: "error",
      tone: "bad",
    });
  });
});

describe("timelineOf for events that are not calls", () => {
  it("notes when part of a result was withheld from the model", () => {
    expect(rowOf("tool.finished", tool("search_runbooks", { quarantined: true }))?.note).toBe(
      "part of the result was withheld from the model",
    );
    expect(rowOf("tool.finished", tool("search_runbooks"))?.note).toBe("");
  });

  it("counts what was loaded for the run, and counts nothing when the lists are missing", () => {
    const loaded = { resources: ["a", "b"], tools: ["x", "y", "z"] };
    expect(rowOf("context.loaded", loaded)?.note).toBe(
      "2 resources loaded · 3 tools listed for this user",
    );
    expect(rowOf("context.loaded")?.note).toBe("0 resources loaded · 0 tools listed for this user");
  });

  it("shows a model turn by its number", () => {
    expect(rowOf("model.delta", { turn: 3 })?.label).toBe("model turn 3");
  });

  it("shows a guardrail with its layer, rule and action", () => {
    const row = rowOf("guardrail.triggered", { layer: "output", rule: "canary", action: "drop" });
    expect(row).toMatchObject({ label: "guardrail · output", tone: "bad", note: "canary: drop" });
  });
});

describe("timelineOf for decisions and endings", () => {
  it("shows an approval in the good tone and any other decision in the bad one", () => {
    expect(rowOf("approval.resolved", { decision: "approved" })).toMatchObject({
      label: "approved",
      tone: "ok",
    });
    expect(rowOf("approval.resolved", { decision: "expired" })).toMatchObject({
      label: "expired",
      tone: "bad",
    });
  });

  it.each([
    ["approval.required", "approval required", "warn", "run paused, state saved"],
    ["run.paused", "paused", "warn", "waiting for the person to sign in again"],
    ["run.completed", "run completed", "ok", ""],
    ["auth.refreshed", "auth", "accent", "Token exchanged again; the same call was repeated"],
  ])("shows %s as %s", (type, label, tone, note) => {
    expect(rowOf(type)).toMatchObject({ label, tone, note, mono: false });
  });

  it("shows why a run stopped or failed by its code", () => {
    expect(rowOf("run.stopped", { code: "budget_exceeded" })).toMatchObject({
      label: "run stopped",
      note: "budget_exceeded",
    });
    expect(rowOf("run.failed", { code: "INTERNAL" })).toMatchObject({
      label: "run failed",
      note: "INTERNAL",
    });
    expect(rowOf("run.failed")?.note).toBe("");
  });
});

describe("rawOf", () => {
  it("is empty for no events", () => {
    expect(rawOf([])).toBe("");
  });

  it("writes one event as it would cross the wire", () => {
    expect(rawOf(events([[0, "run.paused", { code: "reauth_required" }]]))).toBe(
      'id: 1\nevent: run.paused\ndata: {"code":"reauth_required"}',
    );
  });

  it("separates events with a blank line", () => {
    const raw = rawOf(
      events([
        [0, "run.started"],
        [5, "run.completed"],
      ]),
    );
    expect(raw.split("\n\n")).toEqual([
      "id: 1\nevent: run.started\ndata: {}",
      "id: 2\nevent: run.completed\ndata: {}",
    ]);
  });
});

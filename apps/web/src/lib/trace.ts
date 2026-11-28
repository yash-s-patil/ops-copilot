/** What the admin trace shows of a run, worked out from its events in the admin projection. */
import type { AdminEvent } from "@/lib/schemas";
import { own } from "@/lib/own";

export type Span = { key: string; name: string; startMs: number; durationMs: number };

export type TraceBudget = {
  tokens: number;
  toolCalls: number;
  elapsedMs: number;
  cost: string | null;
};

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function number(value: unknown): number {
  return typeof value === "number" ? value : 0;
}

const DECIDED = "approval.resolved";

/**
 * One bar for each tool call, placed by when it finished and how long it took. The time
 * a run spent waiting for a person to decide is left out, as it is from the elapsed time.
 */
export function spansOf(events: readonly AdminEvent[]): { spans: Span[]; totalMs: number } {
  const spans: Span[] = [];
  let totalMs = 1;
  let waitedMs = 0;
  let before: number | null = null;
  const origin = events[0] === undefined ? 0 : new Date(events[0].at).getTime();
  for (const event of events) {
    const at = new Date(event.at).getTime();
    if (event.type === DECIDED && before !== null) waitedMs += at - before;
    before = at;
    const endMs = at - origin - waitedMs;
    totalMs = Math.max(totalMs, endMs);
    if (event.type !== "tool.finished") continue;
    const durationMs = number(event.payload["duration_ms"]);
    spans.push({
      key: `${event.seq}`,
      name: text(event.payload["tool"]),
      startMs: Math.max(0, endMs - durationMs),
      durationMs,
    });
  }
  return { spans, totalMs };
}

export function budgetOf(events: readonly AdminEvent[]): TraceBudget | null {
  const last = events.findLast((event) => event.type === "budget.updated");
  if (last === undefined) return null;
  const { payload } = last;
  const cost = payload["estimated_cost_inr"];
  return {
    tokens: number(payload["tokens_in"]) + number(payload["tokens_out"]),
    toolCalls: number(payload["tool_calls"]),
    elapsedMs: number(payload["elapsed_ms"]),
    cost: typeof cost === "string" ? cost : null,
  };
}

export type TimelineRow = {
  key: string;
  label: string;
  mono: boolean;
  tone: "neutral" | "ok" | "warn" | "bad" | "accent";
  state: string;
  note: string;
  aside: string;
};

type Maker = (event: AdminEvent) => Omit<TimelineRow, "key"> | null;

function toolRow(event: AdminEvent): Omit<TimelineRow, "key"> {
  const { payload } = event;
  const status = text(payload["status"]);
  const phase = text(payload["phase"]);
  const ok = status === "ok";
  const state = !ok
    ? text(payload["error_code"]) || status
    : phase === "propose"
      ? "approval required"
      : phase === "execute"
        ? "executed"
        : "ok";
  return {
    label: text(payload["tool"]),
    mono: true,
    tone: !ok ? "bad" : phase === "propose" ? "warn" : "ok",
    state,
    note: payload["quarantined"] === true ? "part of the result was withheld from the model" : "",
    aside: `${number(payload["duration_ms"])} ms · ${number(payload["bytes"])} B`,
  };
}

const plain = (label: string, tone: TimelineRow["tone"], note: Maker | string): Maker => {
  return (event) => ({
    label,
    mono: false,
    tone,
    state: "",
    note: typeof note === "string" ? note : (note(event)?.note ?? ""),
    aside: "",
  });
};

const MAKERS: Record<string, Maker> = {
  "context.loaded": (event) => ({
    label: "context",
    mono: false,
    tone: "neutral",
    state: "",
    note: `${Array.isArray(event.payload["resources"]) ? event.payload["resources"].length : 0} resources loaded · ${Array.isArray(event.payload["tools"]) ? event.payload["tools"].length : 0} tools listed for this user`,
    aside: "",
  }),
  "model.delta": (event) => ({
    label: `model turn ${number(event.payload["turn"])}`,
    mono: false,
    tone: "neutral",
    state: "",
    note: "",
    aside: "",
  }),
  "tool.finished": toolRow,
  "guardrail.triggered": (event) => ({
    label: `guardrail · ${text(event.payload["layer"])}`,
    mono: false,
    tone: "bad",
    state: "",
    note: `${text(event.payload["rule"])}: ${text(event.payload["action"])}`,
    aside: "",
  }),
  "auth.refreshed": plain("auth", "accent", "Token exchanged again; the same call was repeated"),
  "approval.required": plain("approval required", "warn", "run paused, state saved"),
  "approval.resolved": (event) => ({
    label: text(event.payload["decision"]),
    mono: false,
    tone: event.payload["decision"] === "approved" ? "ok" : "bad",
    state: "",
    note: "",
    aside: "",
  }),
  "run.paused": plain("paused", "warn", "waiting for the person to sign in again"),
  "run.stopped": (event) => ({
    label: "run stopped",
    mono: false,
    tone: "bad",
    state: "",
    note: text(event.payload["code"]),
    aside: "",
  }),
  "run.failed": (event) => ({
    label: "run failed",
    mono: false,
    tone: "bad",
    state: "",
    note: text(event.payload["code"]),
    aside: "",
  }),
  "run.completed": plain("run completed", "ok", ""),
};

export function timelineOf(events: readonly AdminEvent[]): TimelineRow[] {
  const rows: TimelineRow[] = [];
  for (const event of events) {
    const row = own(MAKERS, event.type)?.(event);
    if (row != null) rows.push({ key: `${event.seq}`, ...row });
  }
  return rows;
}

export function rawOf(events: readonly AdminEvent[]): string {
  return events
    .map(
      (event) => `id: ${event.seq}\nevent: ${event.type}\ndata: ${JSON.stringify(event.payload)}`,
    )
    .join("\n\n");
}

/** What an administrator is shown of runs: how they went, never what they were about. */
import type { AdminEvent, AdminRun } from "@/lib/schemas";
import { denied } from "./http";
import { can, PEOPLE, type Person } from "./people";
import { world, type Json, type RunRec, type StoredEvent } from "./world";

const KEPT: Record<string, readonly string[]> = {
  "run.started": ["model"],
  "context.loaded": ["tools", "resources"],
  "model.delta": ["turn", "attempt"],
  "tool.started": ["call_id", "tool", "phase"],
  "tool.finished": [
    "call_id",
    "tool",
    "phase",
    "status",
    "error_code",
    "duration_ms",
    "bytes",
    "quarantined",
  ],
  "approval.required": ["action_id", "tool", "args_hash", "approval_kind", "expires_at"],
  "approval.resolved": ["action_id", "decision"],
  "budget.updated": [
    "tokens_in",
    "tokens_out",
    "estimated_cost_inr",
    "tool_calls",
    "model_turns",
    "elapsed_ms",
  ],
  "run.stopped": ["code"],
  "run.completed": ["escalated"],
  "run.failed": ["code"],
};

export function onlyAdmin(who: Person): void {
  if (!can(who, "admin.read")) throw denied();
}

export function adminView(event: StoredEvent): AdminEvent {
  const payload: Json = {};
  for (const name of KEPT[event.type] ?? []) {
    if (name in event.data) payload[name] = event.data[name];
  }
  return { seq: event.seq, type: event.type, at: event.at, payload };
}

function count(value: unknown): number {
  return typeof value === "number" ? value : 0;
}

export function adminRun(run: RunRec): AdminRun {
  const used = run.events.findLast((event) => event.type === "budget.updated")?.data ?? {};
  const cost = used["estimated_cost_inr"];
  return {
    run_id: run.run_id,
    user_name: PEOPLE[run.owner].name,
    roles: PEOPLE[run.owner].roles,
    ticket_id: run.ticket_id,
    status: run.status,
    model: "scripted",
    tokens_in: count(used["tokens_in"]),
    tokens_out: count(used["tokens_out"]),
    estimated_cost_inr: typeof cost === "string" ? cost : null,
    tool_calls: count(used["tool_calls"]),
    model_turns: count(used["model_turns"]),
    elapsed_ms: count(used["elapsed_ms"]),
    started_at: run.started_at,
  };
}

export function adminRuns(who: Person) {
  onlyAdmin(who);
  return { items: [...world.runs.values()].reverse().map(adminRun) };
}

export function latestRun(): RunRec | null {
  return [...world.runs.values()].at(-1) ?? null;
}

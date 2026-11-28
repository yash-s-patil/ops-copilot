/**
 * The switches a test sets before it acts, so that each test starts from the same story
 * and can ask for one particular thing to go wrong once.
 */
import { invalid, isJson } from "./http";
import { PEOPLE } from "./people";
import { decideOwn, drain, startRun } from "./runs";
import { INVESTIGATE } from "./scripts";
import { reset, TICKETS, world, type Fault, type Json } from "./world";

export const SCENES = ["escalated", "waiting_for_supervisor"] as const;
export type Scene = (typeof SCENES)[number];

function approveOwn(who: (typeof PEOPLE)[keyof typeof PEOPLE], runId: string): void {
  for (const action of [...world.actions.values()]) {
    if (action.run_id !== runId || action.approval_kind !== "self") continue;
    const decision = { decision: "approved", argsHash: action.args_hash, reason: null } as const;
    decideOwn(who, runId, action.action_id, decision);
  }
}

/** Priya has investigated the stuck order and escalated it to L2 with a recommendation. */
function escalated(): void {
  const run = startRun(PEOPLE.priya, INVESTIGATE, TICKETS.stuck);
  drain(run);
  approveOwn(PEOPLE.priya, run.run_id);
  drain(run);
}

/** Arjun has asked for a refund above what he may approve; it waits for a supervisor. */
function waitingForSupervisor(): void {
  const run = startRun(PEOPLE.arjun, INVESTIGATE, TICKETS.lost);
  drain(run);
  approveOwn(PEOPLE.arjun, run.run_id);
}

const SET_SCENE: Record<Scene, () => void> = {
  escalated,
  waiting_for_supervisor: waitingForSupervisor,
};

function isScene(value: unknown): value is Scene {
  return SCENES.some((scene) => scene === value);
}

export function resetTo(body: Json): { reset: true } {
  reset();
  const scenes = Array.isArray(body["scenes"]) ? body["scenes"] : [];
  for (const scene of scenes) {
    if (!isScene(scene)) throw invalid("No scene by that name.");
    SET_SCENE[scene]();
  }
  return { reset: true };
}

const FLAGS = ["fail_next_refund", "runbooks_down", "pause_for_reauth"] as const;

function numberIn(body: Json, name: string): number | undefined {
  const value = body[name];
  return typeof value === "number" && value >= 0 ? value : undefined;
}

export function setSwitches(body: Json) {
  const { switches } = world;
  switches.pace_ms = numberIn(body, "pace_ms") ?? switches.pace_ms;
  switches.payment_step_ms = numberIn(body, "payment_step_ms") ?? switches.payment_step_ms;
  switches.drop_stream_after = numberIn(body, "drop_stream_after") ?? switches.drop_stream_after;
  for (const name of FLAGS) {
    const value = body[name];
    if (typeof value === "boolean") switches[name] = value;
  }
  return switches;
}

export function addFault(body: Json): Fault {
  const { method, path } = body;
  if (typeof method !== "string" || typeof path !== "string") {
    throw invalid("A fault names a method and a path.");
  }
  const fault: Fault = {
    method,
    path,
    times: numberIn(body, "times") ?? 1,
    status: numberIn(body, "status") ?? null,
    retry_after_s: numberIn(body, "retry_after_s") ?? null,
    delay_ms: numberIn(body, "delay_ms") ?? 0,
  };
  world.faults.push(fault);
  return fault;
}

/** The fault this request should meet, if a test asked for one. Each is used up as it is met. */
export function faultFor(method: string, path: string): Fault | null {
  const fault = world.faults.find(
    (one) => one.times > 0 && one.method === method && new RegExp(`^${one.path}$`).test(path),
  );
  if (fault === undefined) return null;
  fault.times -= 1;
  return fault;
}

const CONTROLS: Record<string, (body: Json) => unknown> = {
  "GET /__test/health": () => ({ ok: true }),
  "GET /__test/journal": () => ({ items: world.journal }),
  "POST /__test/reset": resetTo,
  "POST /__test/switches": setSwitches,
  "POST /__test/faults": addFault,
  "POST /__test/faults/clear": () => {
    world.faults = [];
    return { cleared: true };
  },
};

/** The answer to a control request, or null when there is no such control. */
export function control(method: string, path: string, body: unknown): unknown {
  return CONTROLS[`${method} ${path}`]?.(isJson(body) ? body : {}) ?? null;
}

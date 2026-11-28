/** Runs: starting, playing their beats out in time, deciding, carrying out and stopping. */
import { randomUUID } from "node:crypto";
import type { RunView } from "@/lib/schemas";
import { closing } from "./closing";
import { denied, notFound, Refusal } from "./http";
import { can, type Person } from "./people";
import { opening } from "./scripts";
import { ticketFor } from "./tickets";
import { world, type ActionRec, type Json, type RunRec } from "./world";

const NO_RUN = "No investigation with that id is yours.";
const AWAITING = "awaiting_approval";
const REAUTH = "reauth_required";
const ENDS: Record<string, string> = {
  "run.completed": "completed",
  "run.stopped": "stopped",
  "run.failed": "failed",
};

export function emit(run: RunRec, type: string, data: Json): void {
  run.events.push({ seq: run.events.length + 1, type, at: new Date().toISOString(), data });
  const ended = ENDS[type];
  if (ended !== undefined) {
    run.status = run.status === "cancelled" ? run.status : ended;
    run.finished_at = new Date().toISOString();
  }
  for (const listener of [...run.listeners]) listener();
}

/** One beat. False when the run has nothing more to do for now. */
function step(run: RunRec): boolean {
  const beat = run.pending.shift();
  if (beat === undefined || beat === "wait" || beat === "reauth") return false;
  beat.effect?.();
  run.result = beat.result ?? run.result;
  emit(run, beat.type, beat.data);
  const next = run.pending[0];
  if (next !== "wait" && next !== "reauth") return next !== undefined;
  run.pending.shift();
  run.status = next === "wait" ? AWAITING : REAUTH;
  run.sign_ins_at_pause = world.sign_ins.get(run.owner) ?? 0;
  return false;
}

function play(run: RunRec): void {
  run.timer = setTimeout(() => {
    run.timer = null;
    if (step(run)) play(run);
  }, world.switches.pace_ms);
}

/** Plays a run to its next stop at once. Used to set a scene, not by any route. */
export function drain(run: RunRec): void {
  if (run.timer !== null) clearTimeout(run.timer);
  run.timer = null;
  while (step(run));
}

export function startRun(who: Person, message: string, ticketId: number | null): RunRec {
  if (!can(who, "tickets.read")) throw denied();
  const one = ticketId === null ? null : ticketFor(who, ticketId);
  const run: RunRec = {
    run_id: randomUUID(),
    owner: who.key,
    ticket_id: ticketId,
    order_id: one?.order_id ?? null,
    message,
    status: "running",
    started_at: new Date().toISOString(),
    finished_at: null,
    result: null,
    stop_reason: null,
    events: [],
    pending: [],
    actions: [],
    calls: new Map(),
    listeners: new Set(),
    timer: null,
    sign_ins_at_pause: 0,
  };
  run.pending = opening(run, who, one);
  world.runs.set(run.run_id, run);
  play(run);
  return run;
}

/** A run that stopped for sign-in goes on when its owner comes back having signed in again. */
export function nudge(run: RunRec): void {
  const signedInSince = (world.sign_ins.get(run.owner) ?? 0) > run.sign_ins_at_pause;
  if (run.status !== REAUTH || !signedInSince) return;
  run.status = "running";
  play(run);
}

export function ownRun(who: Person, runId: string): RunRec {
  const run = world.runs.get(runId);
  if (run === undefined || run.owner !== who.key) throw notFound(NO_RUN);
  return run;
}

export function viewOf(run: RunRec): RunView {
  return {
    run_id: run.run_id,
    ticket_id: run.ticket_id,
    order_id: run.order_id,
    status: run.status,
    message: run.message,
    started_at: run.started_at,
    finished_at: run.finished_at,
    result: run.result,
    stop_reason: run.stop_reason,
  };
}

export function runsOf(who: Person, askOnly: boolean): RunView[] {
  return [...world.runs.values()]
    .filter((run) => run.owner === who.key && (!askOnly || run.ticket_id === null))
    .reverse()
    .map(viewOf);
}

function resumeIfDecided(run: RunRec): boolean {
  const open = run.actions.some((id) => world.actions.get(id)?.status === "proposed");
  if (run.status !== AWAITING || open) return false;
  run.status = "running";
  run.pending = closing(run);
  play(run);
  return true;
}

function note(
  action: ActionRec,
  who: Person,
  decision: "approved" | "rejected",
  reason: string | null,
) {
  const run = world.runs.get(action.run_id);
  const one = world.tickets.get(action.ticket_id);
  action.status = decision;
  action.approver = who.name;
  action.reason = reason;
  if (action.approval_kind === "supervisor" && one !== undefined && one.refund !== null) {
    one.refund =
      decision === "approved"
        ? { ...one.refund, state: "approved_by_supervisor", approved_by_name: who.name }
        : null;
    one.version += 1;
  }
  if (run === undefined) return;
  const { action_id } = action;
  emit(run, "approval.resolved", { action_id, decision, approver: who.name });
}

function openAction(actionId: string): ActionRec {
  const action = world.actions.get(actionId);
  if (action === undefined) throw notFound("No change with that id is waiting.");
  if (action.status !== "proposed") {
    throw new Refusal("APPROVAL_INVALID", "This was already decided.");
  }
  return action;
}

type Decision = {
  decision: "approved" | "rejected";
  argsHash: string | null;
  reason: string | null;
};

function checked(action: ActionRec, { decision, argsHash, reason }: Decision): void {
  if (decision === "approved" && argsHash !== action.args_hash) {
    throw new Refusal("APPROVAL_INVALID", "What was approved is not what was asked for.");
  }
  if (decision === "rejected" && (reason === null || reason.trim() === "")) {
    throw new Refusal("VALIDATION_FAILED", "A rejection needs a reason.");
  }
}

/** Behind Approve and Reject on a card: the owner of the run decides their own change. */
export function decideOwn(who: Person, runId: string, actionId: string, decision: Decision) {
  const run = ownRun(who, runId);
  const action = openAction(actionId);
  if (action.run_id !== run.run_id)
    throw notFound("That change is not part of this investigation.");
  checked(action, decision);
  if (action.approval_kind === "supervisor" && decision.decision === "approved") {
    throw new Refusal("POLICY_VIOLATION", "A supervisor must approve a refund above INR 5,000.");
  }
  note(action, who, decision.decision, decision.reason);
  return { action_id: actionId, decision: decision.decision, resumed: resumeIfDecided(run) };
}

/** Behind a supervisor's banner. An approval waits for the requester; a rejection ends it. */
export function decideAsSupervisor(who: Person, actionId: string, decision: Decision) {
  if (!can(who, "refunds.approve")) throw denied();
  const action = openAction(actionId);
  checked(action, decision);
  if (action.requester === who.key) {
    throw new Refusal("POLICY_VIOLATION", "You cannot approve your own request.");
  }
  note(action, who, decision.decision, decision.reason);
  const run = world.runs.get(action.run_id);
  if (decision.decision === "rejected" && run !== undefined) resumeIfDecided(run);
  return { action_id: actionId, status: action.status };
}

export function execute(who: Person, actionId: string) {
  const action = world.actions.get(actionId);
  const run = action === undefined ? undefined : world.runs.get(action.run_id);
  if (action === undefined || run === undefined || run.owner !== who.key) {
    throw notFound("No change with that id is yours to carry out.");
  }
  if (run.status !== AWAITING) return { action_id: actionId, resumed_run: null, result: null };
  if (!resumeIfDecided(run)) {
    throw new Refusal("POLICY_VIOLATION", "This is still waiting for a decision.");
  }
  return { action_id: actionId, resumed_run: run.run_id, result: null };
}

export function cancel(who: Person, runId: string) {
  const run = ownRun(who, runId);
  const live = run.status === "running" || run.status === AWAITING;
  if (live) {
    if (run.timer !== null) clearTimeout(run.timer);
    run.timer = null;
    run.pending = [];
    run.status = "cancelled";
    emit(run, "run.stopped", { code: "cancelled", reason: null, known: null });
  }
  return { run_id: run.run_id, stopped: live };
}

export function callDetail(who: Person, runId: string, callId: string) {
  const call = ownRun(who, runId).calls.get(callId);
  if (call === undefined) throw notFound("No step with that id.");
  return { call_id: callId, ...call };
}

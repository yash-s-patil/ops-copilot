/**
 * Turns the events of one run into what the conversation shows.
 *
 * One pure function from the event list to blocks, so that a replayed run and a live one
 * are drawn by the same code and a reconnect can never show a step twice.
 */
import { ZodError } from "zod";
import {
  approvalRequired,
  approvalResolved,
  budgetUpdated,
  guardrailTriggered,
  modelDelta,
  runFailed,
  runPaused,
  runResult,
  runStopped,
  toolFinished,
  toolStarted,
  type ActionOutcome,
  type Budget,
  type RunEvent,
  type RunResult,
  type ToolPhase,
} from "@/lib/events";
import { formatMoney } from "@/lib/format";
import { guardrailLine, reliesOn, WITHHELD_LINE } from "@/lib/step-labels";
import { own } from "@/lib/own";

export type StepState = "running" | "ok" | "prepared" | "denied" | "error";

export type CallStep = {
  kind: "call";
  key: string;
  callId: string;
  tool: string;
  phase: ToolPhase;
  args: Record<string, unknown>;
  state: StepState;
  errorCode: string | null;
  summary: string;
  durationMs: number;
  bytes: number;
  withheld: string[];
};

export type NoticeStep = {
  kind: "notice";
  key: string;
  tone: "bad" | "quiet";
  icon: "shield" | "lock";
  text: string;
  technical: string | null;
};

export type Step = CallStep | NoticeStep;

export type ActionStatus =
  "open" | "waiting" | "approved" | "rejected" | "expired" | "done" | "failed" | "unknown";

export type ActionRow = {
  actionId: string;
  tool: string;
  args: Record<string, unknown>;
  argsHash: string;
  approvalKind: "self" | "supervisor";
  status: ActionStatus;
  approver: string | null;
};

export type Block =
  | { kind: "text"; key: string; text: string }
  | { kind: "steps"; key: string; steps: Step[]; live: boolean }
  | { kind: "approval"; key: string; actions: ActionRow[]; open: boolean }
  | { kind: "decision"; key: string; decision: "approved" | "rejected"; approver: string }
  | { kind: "refund"; key: string }
  | { kind: "summary"; key: string; cause: string; done: string[]; notes: string[] }
  | { kind: "reply"; key: string; draft: string }
  | { kind: "stopped"; key: string; reason: string }
  | { kind: "failed"; key: string; message: string };

/** `awaiting` is this person's own decision; `waiting` is someone else's, a supervisor's. */
export type TurnStatus =
  "running" | "awaiting" | "waiting" | "paused" | "completed" | "stopped" | "failed";

export type Turn = {
  status: TurnStatus;
  blocks: Block[];
  budget: Budget | null;
  lastSeq: number;
  /** What did not answer during this run, in an engineer's words. */
  unavailable: string[];
  /** The run stopped part-way because its owner's sign-in ran out. */
  needsSignIn: boolean;
};

const CURRENCY = "INR";
const STOPPED_BY_OWNER = "You stopped this investigation.";
const BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE";
const REAUTH_REQUIRED = "reauth_required";
const DECIDED = new Set(["approved", "rejected", "expired"]);

type TextEntry = { attempt: number; block: Extract<Block, { kind: "text" }> };
type Card = Extract<Block, { kind: "approval" }>;

class Builder {
  blocks: Block[] = [];
  status: TurnStatus = "running";
  budget: Budget | null = null;
  refundMade = false;
  needsSignIn = false;
  private group: Step[] | null = null;
  private card: Card | null = null;
  private beforeCard: Step[] | null = null;
  private readonly calls = new Map<string, CallStep>();
  private readonly texts = new Map<number, TextEntry>();
  private readonly rows = new Map<string, ActionRow>();
  private readonly said = new Set<string>();

  steps(seq: number): Step[] {
    this.card = null;
    if (this.group === null) {
      this.group = [];
      this.blocks.push({ kind: "steps", key: `steps-${seq}`, steps: this.group, live: false });
    }
    return this.group;
  }

  endGroup(): void {
    this.group = null;
  }

  endCard(): void {
    this.card = null;
  }

  call(key: string): CallStep | undefined {
    return this.calls.get(key);
  }

  addCall(seq: number, step: CallStep): void {
    this.calls.set(step.key, step);
    // Changes are prepared one after another, each followed by its request for approval.
    // The steps stay together above the one card that asks for all of them.
    if (step.phase === "propose" && this.card !== null && this.beforeCard !== null) {
      this.beforeCard.push(step);
      return;
    }
    this.steps(seq).push(step);
  }

  text(turn: number, attempt: number, text: string): void {
    this.endGroup();
    this.endCard();
    const known = this.texts.get(turn);
    if (known === undefined) {
      const block: TextEntry["block"] = { kind: "text", key: `text-${turn}`, text };
      this.texts.set(turn, { attempt, block });
      this.blocks.push(block);
    } else if (attempt > known.attempt) {
      // A turn made again after a restart replaces what the lost attempt had said.
      known.attempt = attempt;
      known.block.text = text;
    } else if (attempt === known.attempt) {
      known.block.text += text;
    }
  }

  row(seq: number, row: ActionRow): void {
    if (this.card === null) {
      this.beforeCard = this.group;
      this.card = { kind: "approval", key: `approval-${seq}`, actions: [], open: false };
      this.blocks.push(this.card);
    }
    this.endGroup();
    this.rows.set(row.actionId, row);
    this.card.actions.push(row);
    // Open only while something on it is this person's to decide; a change that waits
    // for a supervisor is shown, with nothing to press.
    this.card.open = this.card.actions.some((action) => action.status === "open");
  }

  rowOf(actionId: string): ActionRow | undefined {
    return this.rows.get(actionId);
  }

  cardOf(row: ActionRow): Card | undefined {
    return this.blocks.find(
      (block): block is Card => block.kind === "approval" && block.actions.includes(row),
    );
  }

  firstSaid(key: string): boolean {
    if (this.said.has(key)) return false;
    this.said.add(key);
    return true;
  }

  unavailable(): string[] {
    const away = [...this.calls.values()].filter((step) => step.errorCode === BACKEND_UNAVAILABLE);
    return [...new Set(away.map((step) => reliesOn(step.tool)))];
  }

  allRows(): ActionRow[] {
    return [...this.rows.values()];
  }
}

function callKey(callId: string, phase: ToolPhase): string {
  return `${callId}:${phase}`;
}

function onToolStarted(b: Builder, event: RunEvent): void {
  const started = toolStarted.parse(event.data);
  const key = callKey(started.call_id, started.phase);
  if (b.call(key) !== undefined) return;
  b.addCall(event.seq, {
    kind: "call",
    key,
    callId: started.call_id,
    tool: started.tool,
    phase: started.phase,
    args: started.args,
    state: "running",
    errorCode: null,
    summary: "",
    durationMs: 0,
    bytes: 0,
    withheld: [],
  });
}

function stateOf(status: "ok" | "denied" | "error", phase: ToolPhase): StepState {
  if (status !== "ok") return status;
  return phase === "propose" ? "prepared" : "ok";
}

function onToolFinished(b: Builder, event: RunEvent): void {
  const finished = toolFinished.parse(event.data);
  const step = b.call(callKey(finished.call_id, finished.phase));
  if (step === undefined) return;
  step.state = stateOf(finished.status, finished.phase);
  step.errorCode = finished.error_code;
  step.summary = finished.summary;
  step.durationMs = finished.duration_ms;
  step.bytes = finished.bytes;
  step.withheld = finished.withheld;
  if (
    finished.phase === "execute" &&
    finished.tool === "issue_refund" &&
    finished.status === "ok"
  ) {
    b.refundMade = true;
  }
  if (finished.quarantined) {
    b.steps(event.seq).push({
      kind: "notice",
      key: `withheld-${event.seq}`,
      tone: "bad",
      icon: "shield",
      text: WITHHELD_LINE,
      technical: null,
    });
  }
}

function onGuardrail(b: Builder, event: RunEvent): void {
  const triggered = guardrailTriggered.parse(event.data);
  b.steps(event.seq).push({
    kind: "notice",
    key: `guardrail-${event.seq}`,
    tone: "bad",
    icon: "shield",
    text: guardrailLine(triggered.rule),
    technical: `${triggered.layer} · ${triggered.rule}`,
  });
}

function onAuthRefreshed(b: Builder, event: RunEvent): void {
  b.steps(event.seq).push({
    kind: "notice",
    key: `auth-${event.seq}`,
    tone: "quiet",
    icon: "lock",
    text: "Sign-in refreshed automatically",
    technical: null,
  });
}

function onApprovalRequired(b: Builder, event: RunEvent): void {
  const asked = approvalRequired.parse(event.data);
  if (b.rowOf(asked.action_id) !== undefined) return;
  b.row(event.seq, {
    actionId: asked.action_id,
    tool: asked.tool,
    args: asked.args,
    argsHash: asked.args_hash,
    approvalKind: asked.approval_kind,
    status: asked.approval_kind === "self" ? "open" : "waiting",
    approver: null,
  });
}

function decisionLines(card: Card): Block[] {
  const lines = new Map<string, Block>();
  for (const row of card.actions) {
    if (row.approver === null || (row.status !== "approved" && row.status !== "rejected")) continue;
    const key = `${row.status}:${row.approver}`;
    lines.set(key, {
      kind: "decision",
      key: `decision-${card.key}-${key}`,
      decision: row.status,
      approver: row.approver,
    });
  }
  return [...lines.values()];
}

function onApprovalResolved(b: Builder, event: RunEvent): void {
  const resolved = approvalResolved.parse(event.data);
  const row = b.rowOf(resolved.action_id);
  if (row === undefined || !DECIDED.has(resolved.decision) || DECIDED.has(row.status)) return;
  row.status =
    resolved.decision === "approved"
      ? "approved"
      : resolved.decision === "rejected"
        ? "rejected"
        : "expired";
  row.approver = resolved.approver;
  const card = b.cardOf(row);
  if (card === undefined) return;
  card.open = card.actions.some((action) => action.status === "open");
  if (card.open) return;
  // Said once nothing on the card is left for this person to decide, and again for a
  // change a supervisor decides later.
  b.endCard();
  b.endGroup();
  b.blocks.push(...decisionLines(card).filter((line) => b.firstSaid(line.key)));
}

const OUTCOME: Record<string, ActionRow["status"]> = {
  executed: "done",
  failed: "failed",
  unknown: "unknown",
  rejected: "rejected",
  expired: "expired",
};

function doneLine(action: ActionOutcome): string | null {
  if (action.status !== "executed") return null;
  if (action.tool === "issue_refund") {
    const minor = action.result?.["amount_minor"];
    const amount = typeof minor === "number" ? ` of ${formatMoney(minor, CURRENCY)}` : "";
    return `Refund${amount} approved and handed to payments`;
  }
  return action.escalates ? "Note added and ticket escalated to L2" : "Note added to the ticket";
}

function summaryOf(seq: number, result: RunResult): Block {
  const done = result.proposed_actions.map(doneLine).filter((line) => line !== null);
  const undone = result.proposed_actions
    .filter((action) => action.status !== "executed" && action.status !== "rejected")
    .map((action) => action.detail)
    .filter((detail) => detail !== null);
  return {
    kind: "summary",
    key: `summary-${seq}`,
    cause: result.root_cause,
    done,
    notes: [...result.caveats, ...undone],
  };
}

function settleRows(b: Builder, result: RunResult): void {
  for (const outcome of result.proposed_actions) {
    const row = outcome.action_id === null ? undefined : b.rowOf(outcome.action_id);
    const status = own(OUTCOME, outcome.status);
    if (row !== undefined && status !== undefined) row.status = status;
  }
  for (const block of b.blocks) {
    if (block.kind === "approval") block.open = false;
  }
}

function onCompleted(b: Builder, event: RunEvent): void {
  const result = runResult.parse(event.data);
  b.endGroup();
  b.endCard();
  settleRows(b, result);
  if (b.refundMade) b.blocks.push({ kind: "refund", key: `refund-${event.seq}` });
  b.blocks.push(summaryOf(event.seq, result));
  if (result.customer_reply_draft) {
    b.blocks.push({ kind: "reply", key: `reply-${event.seq}`, draft: result.customer_reply_draft });
  }
  b.status = "completed";
}

function onStopped(b: Builder, event: RunEvent): void {
  const stopped = runStopped.parse(event.data);
  b.endGroup();
  b.endCard();
  for (const block of b.blocks) {
    if (block.kind === "approval") block.open = false;
  }
  const reason = stopped.code === "cancelled" ? STOPPED_BY_OWNER : (stopped.reason ?? "");
  b.blocks.push({ kind: "stopped", key: `stopped-${event.seq}`, reason });
  b.status = "stopped";
}

function onFailed(b: Builder, event: RunEvent): void {
  const failed = runFailed.parse(event.data);
  b.endGroup();
  b.endCard();
  b.blocks.push({ kind: "failed", key: `failed-${event.seq}`, message: failed.message });
  b.status = "failed";
}

const HANDLERS: Record<string, (b: Builder, event: RunEvent) => void> = {
  "tool.started": onToolStarted,
  "tool.finished": onToolFinished,
  "model.delta": (b, event) => {
    const delta = modelDelta.parse(event.data);
    b.text(delta.turn, delta.attempt, delta.text);
  },
  "guardrail.triggered": onGuardrail,
  "auth.refreshed": onAuthRefreshed,
  "approval.required": onApprovalRequired,
  "approval.resolved": onApprovalResolved,
  "budget.updated": (b, event) => {
    b.budget = budgetUpdated.parse(event.data);
  },
  "run.paused": (b, event) => {
    b.status = "paused";
    const paused = runPaused.safeParse(event.data);
    b.needsSignIn = paused.success && paused.data.code === REAUTH_REQUIRED;
  },
  "run.completed": onCompleted,
  "run.stopped": onStopped,
  "run.failed": onFailed,
};

const RESUMES = new Set(["tool.started", "model.delta", "approval.resolved"]);

/** A run with a change still to be decided does nothing until it is. */
function parked(rows: readonly ActionRow[]): TurnStatus {
  if (rows.some((row) => row.status === "open")) return "awaiting";
  return rows.some((row) => row.status === "waiting") ? "waiting" : "running";
}

function finish(b: Builder, lastSeq: number): Turn {
  const status = b.status === "running" ? parked(b.allRows()) : b.status;
  for (const block of b.blocks) if (block.kind === "steps") block.live = false;
  const last = b.blocks.at(-1);
  if (last?.kind === "steps" && status === "running") last.live = true;
  return {
    status,
    blocks: [...b.blocks],
    budget: b.budget,
    lastSeq,
    unavailable: b.unavailable(),
    needsSignIn: b.needsSignIn,
  };
}

/**
 * One event that cannot be read is left out, so that it does not take the rest of the
 * conversation with it. Anything else that goes wrong is a defect and is not hidden.
 */
function applied(b: Builder, event: RunEvent): void {
  try {
    own(HANDLERS, event.type)?.(b, event);
  } catch (error) {
    if (!(error instanceof ZodError)) throw error;
  }
}

/**
 * Follows one run as its events arrive: each event is applied once, in order, and only
 * the new ones are read each time, so a long stream of text costs no more per piece than
 * a short one. Events may repeat; one at or below the last applied is skipped.
 */
export class TurnFollower {
  private readonly b = new Builder();
  private lastSeq = 0;

  feed(events: readonly RunEvent[]): Turn {
    for (const event of events) {
      if (event.seq <= this.lastSeq) continue;
      this.lastSeq = event.seq;
      if (this.b.status === "paused" && RESUMES.has(event.type)) {
        this.b.status = "running";
        this.b.needsSignIn = false;
      }
      applied(this.b, event);
    }
    return finish(this.b, this.lastSeq);
  }
}

/** The conversation blocks of one run. */
export function turnOf(events: readonly RunEvent[]): Turn {
  return new TurnFollower().feed(events);
}

export function stepCounts(steps: readonly Step[]): {
  calls: number;
  durationMs: number;
  issues: number;
} {
  let calls = 0;
  let durationMs = 0;
  let issues = 0;
  for (const step of steps) {
    if (step.kind === "notice") {
      if (step.tone === "bad") issues += 1;
      continue;
    }
    calls += 1;
    durationMs += step.durationMs;
    if (step.state === "error" || step.state === "denied") issues += 1;
  }
  return { calls, durationMs, issues };
}

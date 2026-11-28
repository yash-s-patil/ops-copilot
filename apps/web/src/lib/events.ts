import { z } from "zod";

/** One event of a run as the agent streams it: a number, a type and a payload. */
export type RunEvent = { seq: number; type: string; data: unknown };

const args = z.record(z.string(), z.unknown());

export const toolStarted = z.object({
  call_id: z.string(),
  tool: z.string(),
  phase: z.enum(["read", "propose", "execute"]),
  args: args.default({}),
});

export const toolFinished = z.object({
  call_id: z.string(),
  tool: z.string(),
  phase: z.enum(["read", "propose", "execute"]),
  status: z.enum(["ok", "denied", "error"]),
  error_code: z.string().nullable().default(null),
  summary: z.string().default(""),
  duration_ms: z.number().default(0),
  bytes: z.number().default(0),
  quarantined: z.boolean().default(false),
  withheld: z.array(z.string()).default([]),
});

export const modelDelta = z.object({
  turn: z.number(),
  attempt: z.number(),
  text: z.string(),
});

export const guardrailTriggered = z.object({
  layer: z.string(),
  rule: z.string(),
  action: z.string(),
  detail: z.string().default(""),
});

export const approvalRequired = z.object({
  action_id: z.string(),
  call_id: z.string().default(""),
  tool: z.string(),
  args,
  args_hash: z.string(),
  approval_kind: z.enum(["self", "supervisor"]),
  summary: z.string().default(""),
  expires_at: z.string(),
});

export const approvalResolved = z.object({
  action_id: z.string(),
  decision: z.string(),
  approver: z.string().nullable().default(null),
});

export const budgetUpdated = z.object({
  tokens_in: z.number(),
  tokens_out: z.number(),
  estimated_cost_inr: z.string().nullable().default(null),
  tool_calls: z.number(),
  model_turns: z.number(),
  elapsed_ms: z.number(),
});

export const actionOutcome = z.object({
  call_id: z.string(),
  tool: z.string(),
  status: z.string(),
  action_id: z.string().nullable().default(null),
  escalates: z.boolean().default(false),
  detail: z.string().nullable().default(null),
  result: args.nullable().default(null),
});

export const runResult = z.object({
  root_cause: z.string().default(""),
  proposed_actions: z.array(actionOutcome).default([]),
  escalated: z.boolean().default(false),
  customer_reply_draft: z.string().nullable().default(null),
  caveats: z.array(z.string()).default([]),
});

export const runStopped = z.object({
  reason: z.string().nullable().default(null),
  code: z.string().nullable().default(null),
  known: runResult.nullable().default(null),
});

export const runFailed = z.object({ code: z.string(), message: z.string() });

export const runPaused = z.object({ code: z.string() });

export type ToolPhase = z.infer<typeof toolStarted>["phase"];
export type ActionOutcome = z.infer<typeof actionOutcome>;
export type RunResult = z.infer<typeof runResult>;
export type Budget = z.infer<typeof budgetUpdated>;

export const FINAL_EVENTS = new Set(["run.completed", "run.stopped", "run.failed"]);

/** Every kind of event a run's stream sends, each by its own name. */
export const STREAMED_EVENTS = [
  "run.started",
  "context.loaded",
  "model.delta",
  "tool.started",
  "tool.finished",
  "auth.refreshed",
  "guardrail.triggered",
  "approval.required",
  "approval.resolved",
  "budget.updated",
  "run.paused",
  "run.stopped",
  "run.completed",
  "run.failed",
] as const;

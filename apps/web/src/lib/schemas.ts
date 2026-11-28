/** The payloads that cross the network, each checked as it arrives. */
import { z } from "zod";
import { runResult } from "@/lib/events";

export const VIEWS = ["my_queue", "all_open", "waiting", "done"] as const;
export const view = z.enum(VIEWS);
export type View = z.infer<typeof view>;

export const me = z.object({
  name: z.string(),
  roles: z.array(z.string()),
  region: z.string().nullable(),
  permissions: z.array(z.string()),
  demo_tools: z.boolean(),
});
export type Me = z.infer<typeof me>;

const refundState = z.enum([
  "waiting_for_supervisor",
  "approved_by_supervisor",
  "approved",
  "sent_to_payments",
  "paid",
  "failed",
]);
export type RefundState = z.infer<typeof refundState>;

const priority = z.enum(["P1", "P2", "P3", "P4"]);
export type Priority = z.infer<typeof priority>;

export const ticketSummary = z.object({
  ticket_id: z.number(),
  order_id: z.number(),
  status: z.string(),
  priority,
  queue: z.string(),
  summary: z.string(),
  customer_name: z.string(),
  customer_tier: z.string(),
  escalated_by_name: z.string().nullable(),
  has_recommendation: z.boolean(),
  refund_state: refundState.nullable(),
  needs_my_approval: z.boolean(),
  updated_at: z.string(),
});
export type TicketSummary = z.infer<typeof ticketSummary>;

export const ticketPage = z.object({
  items: z.array(ticketSummary),
  next_cursor: z.string().nullable(),
  counts: z.record(view, z.number()),
});
export type TicketPage = z.infer<typeof ticketPage>;

const note = z.object({
  author_kind: z.enum(["customer", "engineer", "system", "agent_on_behalf"]),
  author_name: z.string(),
  body: z.string(),
  created_at: z.string(),
});
export type Note = z.infer<typeof note>;

const refund = z.object({
  state: refundState,
  amount_minor: z.number(),
  currency: z.string(),
  reason_code: z.string(),
  requested_by_name: z.string(),
  approved_by_name: z.string().nullable(),
  failure_reason: z.string().nullable(),
  refund_id: z.number().nullable(),
});
export type Refund = z.infer<typeof refund>;

const recommendation = z.object({
  amount_minor: z.number(),
  reason_code: z.string(),
  reason_text: z.string(),
});

const ticket = z.object({
  ticket_id: z.number(),
  order_id: z.number(),
  customer_id: z.number(),
  status: z.string(),
  priority,
  queue: z.string(),
  summary: z.string(),
  escalated_by_name: z.string().nullable(),
  escalated_at: z.string().nullable(),
  recommendation: recommendation.nullable(),
  version: z.number(),
  refund: refund.nullable(),
  notes: z.array(note),
});
export type Ticket = z.infer<typeof ticket>;

const orderEvent = z.object({ type: z.string(), summary: z.string() });

const order = z.object({
  order_id: z.number(),
  status: z.string(),
  status_age_hours: z.number(),
  is_stuck: z.boolean(),
  amount_minor: z.number(),
  currency: z.string(),
  events: z.object({ recent: z.array(orderEvent) }).nullable(),
});
export type Order = z.infer<typeof order>;

const customer = z.object({
  display_name: z.string(),
  tier: z.string(),
  email: z.string(),
  phone: z.string().nullable(),
  contact_masked: z.boolean(),
});
export type Customer = z.infer<typeof customer>;

const waitingAction = z.object({
  action_id: z.string(),
  tool: z.string(),
  status: z.string(),
  approval_kind: z.string(),
  summary: z.string(),
  args: z.record(z.string(), z.unknown()),
  args_hash: z.string(),
  policy_snapshot: z.record(z.string(), z.unknown()).nullable(),
  requester_name: z.string(),
  created_at: z.string(),
  mine: z.boolean(),
  may_decide: z.boolean(),
});
export type WaitingAction = z.infer<typeof waitingAction>;

const investigation = z.object({
  run_id: z.string(),
  by_name: z.string(),
  mine: z.boolean(),
  status: z.string(),
  started_at: z.string(),
  finished_at: z.string().nullable(),
  root_cause: z.string().nullable(),
  actions: z.array(z.object({ tool: z.string().nullable(), status: z.string().nullable() })),
});
export type Investigation = z.infer<typeof investigation>;

export const workspace = z.object({
  ticket,
  order: order.nullable(),
  customer: customer.nullable(),
  approvals: z.array(waitingAction),
  investigations: z.array(investigation),
});
export type Workspace = z.infer<typeof workspace>;

export const ticketStatus = z.object({
  ticket_id: z.number(),
  status: z.string(),
  queue: z.string(),
  refund_state: refundState.nullable(),
  version: z.number(),
});

export const runView = z.object({
  run_id: z.string(),
  ticket_id: z.number().nullable(),
  order_id: z.number().nullable(),
  status: z.string(),
  message: z.string(),
  started_at: z.string(),
  finished_at: z.string().nullable(),
  result: runResult.nullable(),
  stop_reason: z.string().nullable(),
});
export type RunView = z.infer<typeof runView>;

export const runList = z.object({ items: z.array(runView) });
export const started = z.object({ run_id: z.string() });
export const decided = z.object({
  action_id: z.string(),
  decision: z.string(),
  resumed: z.boolean(),
});
export const executed = z.object({
  action_id: z.string(),
  resumed_run: z.string().nullable(),
});
export const resolved = z.object({
  ticket_id: z.number(),
  status: z.string(),
  version: z.number(),
});
export const anything = z.unknown();

export const callDetail = z.object({
  call_id: z.string(),
  tool: z.string(),
  args: z.record(z.string(), z.unknown()),
  result: z.string().nullable(),
});

export const adminRun = z.object({
  run_id: z.string(),
  user_name: z.string(),
  roles: z.array(z.string()),
  ticket_id: z.number().nullable(),
  status: z.string(),
  model: z.string(),
  tokens_in: z.number(),
  tokens_out: z.number(),
  estimated_cost_inr: z.string().nullable(),
  tool_calls: z.number(),
  model_turns: z.number(),
  elapsed_ms: z.number(),
  started_at: z.string(),
});
export type AdminRun = z.infer<typeof adminRun>;

export const adminEvent = z.object({
  seq: z.number(),
  type: z.string(),
  at: z.string(),
  payload: z.record(z.string(), z.unknown()),
});
export type AdminEvent = z.infer<typeof adminEvent>;

export const adminRuns = z.object({ items: z.array(adminRun) });
export const adminRunDetail = z.object({ run: adminRun, events: z.array(adminEvent) });

export const errorBody = z.object({
  code: z.string(),
  message: z.string(),
  details: z.record(z.string(), z.unknown()).default({}),
});

/** What a run does, written out beat by beat. There is no model here: the story is fixed. */
import { createHash, randomUUID } from "node:crypto";
import type { RunResult } from "@/lib/events";
import type { Order } from "@/lib/schemas";
import { can, PEOPLE, type Person } from "./people";
import { visibleOrder } from "./tickets";
import { world, type ActionRec, type Beat, type Json, type RunRec, type TicketRec } from "./world";

export const INVESTIGATE = "Investigate this ticket and propose a resolution.";
export const SELF_APPROVAL_CAP_MINOR = 500_000;
const PREPARED = "Prepared only. Nothing changes until you approve.";
const NOT_VISIBLE = "Nothing with that number is visible to you.";
const RUNBOOK_MATCHES =
  "Matches: Address validation failed; Order stuck in packed over 48 hours; Goodwill refund for delay.";
const GOODWILL_SHARE = 0.1;
const APPROVAL_LIFETIME_MS = 120_000;
const BASE_DURATION_MS = 120;
const DURATION_STEP_MS = 40;
const TOKENS_PER_CALL = 900;

type Proposal = { tool: ActionRec["tool"]; args: Json; kind: ActionRec["approval_kind"] };

function callId(run: RunRec, tool: string, args: Json, result: string | null): string {
  const id = `call_${run.calls.size + 1}`;
  run.calls.set(id, { tool, args, result });
  return id;
}

function finished(run: RunRec, call_id: string, tool: string, phase: string, more: Json): Beat {
  return {
    type: "tool.finished",
    data: {
      call_id,
      tool,
      phase,
      status: "ok",
      error_code: null,
      duration_ms: BASE_DURATION_MS + DURATION_STEP_MS * run.calls.size,
      bytes: 480 + 64 * run.calls.size,
      quarantined: false,
      withheld: [],
      ...more,
    },
  };
}

function read(run: RunRec, tool: string, args: Json, summary: string): Beat[] {
  const call_id = callId(run, tool, args, JSON.stringify({ summary }));
  return [
    { type: "tool.started", data: { call_id, tool, phase: "read", args } },
    finished(run, call_id, tool, "read", { summary }),
  ];
}

function said(turn: number, text: string): Beat[] {
  const words = text.split(" ");
  const half = Math.ceil(words.length / 2);
  const parts = [words.slice(0, half).join(" "), ` ${words.slice(half).join(" ")}`];
  return parts.map((part) => ({ type: "model.delta", data: { turn, attempt: 1, text: part } }));
}

export function budget(run: RunRec, model_turns: number): Beat {
  return {
    type: "budget.updated",
    data: {
      tokens_in: TOKENS_PER_CALL * run.calls.size,
      tokens_out: 60 * model_turns,
      estimated_cost_inr: "0.42",
      tool_calls: run.calls.size,
      model_turns,
      elapsed_ms: (BASE_DURATION_MS + DURATION_STEP_MS) * run.calls.size,
    },
  };
}

function opened(): Beat[] {
  return [
    { type: "run.started", data: { model: "scripted" } },
    {
      type: "context.loaded",
      data: {
        tools: ["get_order", "lookup_customer", "get_ticket", "search_runbooks"],
        resources: [{ uri: "policy://refund-policy", version: "3" }],
      },
    },
  ];
}

function causeOf(order: Order): string {
  if (order.status === "lost") {
    return "The carrier confirmed the parcel lost in transit, so the order cannot be delivered.";
  }
  const last = order.events?.recent[0]?.summary ?? "no event recorded";
  return `The order has been ${order.status} for ${order.status_age_hours} hours. Last event: ${last}.`;
}

function refundFor(order: Order): {
  amount_minor: number;
  reason_code: string;
  reason_text: string;
} {
  if (order.status === "lost") {
    return {
      amount_minor: order.amount_minor,
      reason_code: "lost_in_transit",
      reason_text: "Full refund: the carrier confirmed the parcel lost.",
    };
  }
  return {
    amount_minor: Math.round(order.amount_minor * GOODWILL_SHARE),
    reason_code: "goodwill_delay",
    reason_text: "Goodwill refund of 10% for a delay over 48 hours.",
  };
}

function runbooks(run: RunRec, one: TicketRec): Beat[] {
  const args = { query: one.summary };
  if (!world.switches.runbooks_down) return read(run, "search_runbooks", args, RUNBOOK_MATCHES);
  const call_id = callId(run, "search_runbooks", args, null);
  return [
    { type: "tool.started", data: { call_id, tool: "search_runbooks", phase: "read", args } },
    finished(run, call_id, "search_runbooks", "read", {
      status: "error",
      error_code: "BACKEND_UNAVAILABLE",
      summary: "This is unavailable right now. Continuing without it.",
    }),
  ];
}

/** The owner's sign-in ran out half way: the run says so and waits for them to come back. */
function reauth(): Beat[] {
  if (!world.switches.pause_for_reauth) return [];
  world.switches.pause_for_reauth = false;
  return [{ type: "run.paused", data: { code: "reauth_required" } }, "reauth"];
}

function reads(run: RunRec, one: TicketRec, order: Order): Beat[] {
  const customer = world.customers.get(one.customer_id);
  return [
    ...read(run, "get_order", { order_id: order.order_id }, causeOf(order)),
    ...read(
      run,
      "lookup_customer",
      { customer_id: one.customer_id },
      `${customer?.display_name ?? "Unknown"}, ${customer?.tier ?? "standard"} customer.`,
    ),
    ...read(
      run,
      "get_ticket",
      { ticket_id: one.ticket_id },
      `${one.notes.length} notes on the ticket.`,
    ),
    ...runbooks(run, one),
    ...read(
      run,
      "get_policy_document",
      { name: "refund-policy", section: "limits" },
      "L2 may refund up to INR 5,000 alone. Above that a supervisor approves.",
    ),
  ];
}

function register(run: RunRec, one: TicketRec, proposal: Proposal, action: ActionRec): void {
  world.actions.set(action.action_id, action);
  run.actions.push(action.action_id);
  if (proposal.tool !== "issue_refund" || proposal.kind !== "supervisor") return;
  const amount = proposal.args["amount_minor"];
  one.refund_requester = run.owner;
  one.refund = {
    state: "waiting_for_supervisor",
    amount_minor: typeof amount === "number" ? amount : 0,
    currency: "INR",
    reason_code: String(proposal.args["reason_code"]),
    requested_by_name: PEOPLE[run.owner].name,
    approved_by_name: null,
    failure_reason: null,
    refund_id: null,
  };
}

function propose(run: RunRec, one: TicketRec, proposal: Proposal): Beat[] {
  const { tool, args, kind } = proposal;
  const call_id = callId(run, tool, args, JSON.stringify({ status: "approval_required" }));
  const action: ActionRec = {
    action_id: randomUUID(),
    run_id: run.run_id,
    ticket_id: one.ticket_id,
    call_id,
    tool,
    args,
    args_hash: createHash("sha256").update(JSON.stringify(args)).digest("hex"),
    approval_kind: kind,
    status: "proposed",
    requester: run.owner,
    approver: null,
    reason: null,
    created_at: new Date().toISOString(),
  };
  const { action_id, args_hash } = action;
  const expires_at = new Date(Date.now() + APPROVAL_LIFETIME_MS).toISOString();
  return [
    { type: "tool.started", data: { call_id, tool, phase: "propose", args } },
    finished(run, call_id, tool, "propose", { summary: PREPARED }),
    {
      type: "approval.required",
      data: {
        action_id,
        call_id,
        tool,
        args,
        args_hash,
        approval_kind: kind,
        summary: "",
        expires_at,
      },
      effect: () => register(run, one, proposal, action),
    },
  ];
}

function proposals(who: Person, one: TicketRec, order: Order): Proposal[] {
  const refund = refundFor(order);
  const { ticket_id } = one;
  if (!can(who, "refunds.request")) {
    const note = `${causeOf(order)} A refund needs L2. Recommended: ${refund.reason_text}`;
    return [
      {
        tool: "update_ticket",
        kind: "self",
        args: { ticket_id, note, escalate_to: "L2", recommendation: refund },
      },
    ];
  }
  const kind = refund.amount_minor > SELF_APPROVAL_CAP_MINOR ? "supervisor" : "self";
  return [
    { tool: "issue_refund", kind, args: { order_id: order.order_id, ...refund } },
    {
      tool: "update_ticket",
      kind: "self",
      args: { ticket_id, note: `${causeOf(order)} ${refund.reason_text}` },
    },
  ];
}

function investigation(run: RunRec, who: Person, one: TicketRec, order: Order): Beat[] {
  return [
    ...opened(),
    ...said(
      1,
      `I will check order ${order.order_id}, the ticket, the runbooks and the refund policy.`,
    ),
    ...reads(run, one, order),
    ...reauth(),
    ...proposals(who, one, order).flatMap((proposal) => propose(run, one, proposal)),
    budget(run, 1),
    "wait",
  ];
}

export function completed(root_cause: string, more: Partial<RunResult> = {}): Beat {
  const result: RunResult = {
    root_cause,
    proposed_actions: [],
    escalated: false,
    customer_reply_draft: null,
    caveats: [],
    ...more,
  };
  return { type: "run.completed", data: { ...result }, result };
}

function followUp(run: RunRec, order: Order): Beat[] {
  return [
    ...opened(),
    ...read(run, "get_order", { order_id: order.order_id }, causeOf(order)),
    ...said(
      1,
      `Order ${order.order_id} is still ${order.status}. Nothing has changed since the last check.`,
    ),
    budget(run, 1),
    completed("Nothing has changed since the last check."),
  ];
}

function notFound(run: RunRec, orderId: number): Beat[] {
  const args = { order_id: orderId };
  const call_id = callId(run, "get_order", args, null);
  return [
    ...opened(),
    { type: "tool.started", data: { call_id, tool: "get_order", phase: "read", args } },
    finished(run, call_id, "get_order", "read", {
      status: "error",
      error_code: "NOT_FOUND",
      summary: NOT_VISIBLE,
    }),
    ...said(1, `I cannot find order ${orderId}. It may not exist, or it may not be yours to see.`),
    budget(run, 1),
    completed(`Order ${orderId} could not be found.`),
  ];
}

function answer(run: RunRec, who: Person): Beat[] {
  const named = /\b(\d{4})\b/.exec(run.message)?.[1];
  if (named === undefined) {
    return [
      ...opened(),
      ...said(1, "Tell me an order number and I will look at it."),
      completed("No order was named."),
    ];
  }
  const order = visibleOrder(who, Number(named));
  if (order === null) return notFound(run, Number(named));
  return [
    ...opened(),
    ...read(run, "get_order", { order_id: order.order_id }, causeOf(order)),
    ...said(1, `Order ${order.order_id}: ${causeOf(order)}`),
    budget(run, 1),
    completed(causeOf(order)),
  ];
}

/** Everything a new run does until it ends or has to wait for a person. */
export function opening(run: RunRec, who: Person, one: TicketRec | null): Beat[] {
  if (one === null) return answer(run, who);
  const order = world.orders.get(one.order_id);
  if (order === undefined) return notFound(run, one.order_id);
  return run.message === INVESTIGATE ? investigation(run, who, one, order) : followUp(run, order);
}

export { causeOf };

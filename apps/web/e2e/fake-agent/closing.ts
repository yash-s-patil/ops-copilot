/** What a run does once people have decided: carry out what was approved, then sum up. */
import type { ActionOutcome } from "@/lib/events";
import { PEOPLE } from "./people";
import { budget, causeOf, completed } from "./scripts";
import { handToPayments } from "./tickets";
import { touch, world, type ActionRec, type Beat, type RunRec, type TicketRec } from "./world";

const DONE = "Done, after approval.";

function addNote(one: TicketRec, run: RunRec, body: string): void {
  one.notes.unshift({
    author_kind: "agent_on_behalf",
    author_name: PEOPLE[run.owner].name,
    body,
    created_at: new Date().toISOString(),
  });
}

function changeTicket(one: TicketRec, run: RunRec, action: ActionRec): void {
  addNote(one, run, String(action.args["note"] ?? ""));
  if (action.args["escalate_to"] != null) {
    const advice = action.args["recommendation"];
    one.queue = "L2";
    one.escalated_by = run.owner;
    one.escalated_by_name = PEOPLE[run.owner].name;
    one.escalated_at = new Date().toISOString();
    one.recommendation =
      typeof advice === "object" && advice !== null
        ? {
            amount_minor: Number(Reflect.get(advice, "amount_minor")),
            reason_code: String(Reflect.get(advice, "reason_code")),
            reason_text: String(Reflect.get(advice, "reason_text")),
          }
        : null;
  }
  touch(one);
}

function recordRefund(one: TicketRec, run: RunRec, action: ActionRec): void {
  one.refund = {
    state: "approved",
    amount_minor: Number(action.args["amount_minor"]),
    currency: "INR",
    reason_code: String(action.args["reason_code"]),
    requested_by_name: PEOPLE[run.owner].name,
    approved_by_name: action.approver,
    failure_reason: null,
    refund_id: world.next_refund_id,
  };
  world.next_refund_id += 1;
  one.refund_requester = run.owner;
  touch(one);
  handToPayments(one);
}

function carryOut(run: RunRec, action: ActionRec): void {
  const one = world.tickets.get(action.ticket_id);
  action.status = "executed";
  if (one === undefined) return;
  if (action.tool === "issue_refund") recordRefund(one, run, action);
  else changeTicket(one, run, action);
}

function executed(run: RunRec, action: ActionRec): Beat[] {
  const { call_id, tool, args } = action;
  return [
    { type: "tool.started", data: { call_id, tool, phase: "execute", args } },
    {
      type: "tool.finished",
      data: {
        call_id,
        tool,
        phase: "execute",
        status: "ok",
        error_code: null,
        summary: DONE,
        duration_ms: 180,
        bytes: 210,
        quarantined: false,
        withheld: [],
      },
      effect: () => carryOut(run, action),
    },
  ];
}

function outcomeOf(action: ActionRec): ActionOutcome {
  const approved = action.status === "approved";
  return {
    call_id: action.call_id,
    tool: action.tool,
    status: approved ? "executed" : "rejected",
    action_id: action.action_id,
    escalates: action.args["escalate_to"] != null,
    detail: action.reason,
    result:
      approved && action.tool === "issue_refund"
        ? { amount_minor: action.args["amount_minor"] }
        : null,
  };
}

function replyDraft(run: RunRec, approved: readonly ActionRec[]): string | null {
  const refund = approved.find((action) => action.tool === "issue_refund");
  const one = run.ticket_id === null ? undefined : world.tickets.get(run.ticket_id);
  const customer = one === undefined ? undefined : world.customers.get(one.customer_id);
  if (refund === undefined || customer === undefined) return null;
  const first = customer.display_name.split(" ")[0] ?? customer.display_name;
  return `Hello ${first}, we are sorry about your order. A refund has been approved and will reach you in a few days.`;
}

/** Called when every change the run asked for has been decided. */
export function closing(run: RunRec): Beat[] {
  const actions = run.actions
    .map((id) => world.actions.get(id))
    .filter((action) => action !== undefined);
  const approved = actions.filter((action) => action.status === "approved");
  const order = run.order_id === null ? undefined : world.orders.get(run.order_id);
  const outcomes = actions.map(outcomeOf);
  return [
    ...approved.flatMap((action) => executed(run, action)),
    budget(run, 2),
    completed(order === undefined ? "" : causeOf(order), {
      proposed_actions: outcomes,
      escalated: outcomes.some((outcome) => outcome.escalates && outcome.status === "executed"),
      customer_reply_draft: replyDraft(run, approved),
    }),
  ];
}

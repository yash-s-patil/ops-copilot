/** The inbox views, the ticket workspace, resolving, and the payments that follow a refund. */
import type {
  Customer,
  Investigation,
  RefundState,
  TicketPage,
  TicketSummary,
  View,
  WaitingAction,
  Workspace,
} from "@/lib/schemas";
import { denied, notFound, Refusal } from "./http";
import { can, has, PEOPLE, ROLE, type Person } from "./people";
import { later, touch, world, type ActionRec, type RunRec, type TicketRec } from "./world";

const VIEW_NAMES = ["my_queue", "all_open", "waiting", "done"] as const;
const IN_FLIGHT: ReadonlySet<RefundState> = new Set([
  "waiting_for_supervisor",
  "approved_by_supervisor",
  "approved",
  "sent_to_payments",
]);
const NO_TICKET = "No ticket with that number is visible to you.";
const MASK = "•••";

function inFlight(one: TicketRec): boolean {
  return one.refund !== null && IN_FLIGHT.has(one.refund.state);
}

function sees(who: Person, one: TicketRec): boolean {
  return who.region === "*" || who.region === one.region;
}

function needsApprovalFrom(who: Person, one: TicketRec): boolean {
  return (
    can(who, "refunds.approve") &&
    one.refund?.state === "waiting_for_supervisor" &&
    one.refund_requester !== who.key
  );
}

function inMyQueue(who: Person, one: TicketRec): boolean {
  if (has(who, ROLE.supervisor)) return needsApprovalFrom(who, one);
  if (one.status !== "open") return false;
  return has(who, ROLE.l2) ? one.queue === "L2" && !inFlight(one) : one.queue === "L1";
}

function isWaiting(who: Person, one: TicketRec): boolean {
  if (one.status !== "open" || has(who, ROLE.supervisor)) return false;
  return has(who, ROLE.l2) ? inFlight(one) : one.escalated_by === who.key && one.queue === "L2";
}

const IN_VIEW: Record<View, (who: Person, one: TicketRec) => boolean> = {
  my_queue: inMyQueue,
  all_open: (_who, one) => one.status === "open",
  waiting: isWaiting,
  done: (_who, one) => one.status === "resolved",
};

function summaryOf(who: Person, one: TicketRec): TicketSummary {
  const customer = world.customers.get(one.customer_id);
  return {
    ticket_id: one.ticket_id,
    order_id: one.order_id,
    status: one.status,
    priority: one.priority,
    queue: one.queue,
    summary: one.summary,
    customer_name: customer?.display_name ?? "",
    customer_tier: customer?.tier ?? "",
    escalated_by_name: one.escalated_by_name,
    has_recommendation: one.recommendation !== null,
    refund_state: one.refund?.state ?? null,
    needs_my_approval: needsApprovalFrom(who, one),
    updated_at: one.updated_at,
  };
}

function engineer(who: Person): void {
  if (!can(who, "tickets.read")) throw denied();
}

export function isView(value: string | null): value is View {
  return VIEW_NAMES.some((name) => name === value);
}

export function inbox(who: Person, view: View): TicketPage {
  engineer(who);
  const visible = [...world.tickets.values()].filter((one) => sees(who, one));
  const of = (name: View) => visible.filter((one) => IN_VIEW[name](who, one));
  return {
    items: of(view)
      .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
      .map((one) => summaryOf(who, one)),
    next_cursor: null,
    counts: {
      my_queue: of("my_queue").length,
      all_open: of("all_open").length,
      waiting: of("waiting").length,
      done: of("done").length,
    },
  };
}

export function ticketFor(who: Person, ticketId: number): TicketRec {
  engineer(who);
  const one = world.tickets.get(ticketId);
  if (one === undefined || !sees(who, one)) throw notFound(NO_TICKET);
  return one;
}

function masked(who: Person, customer: Customer): Customer {
  if (can(who, "pii.read")) return customer;
  const domain = customer.email.slice(customer.email.indexOf("@"));
  return {
    ...customer,
    email: `${customer.email.charAt(0)}${MASK}${domain}`,
    phone: customer.phone === null ? null : `${MASK} ${customer.phone.slice(-2)}`,
    contact_masked: true,
  };
}

function waitingFor(who: Person, action: ActionRec): WaitingAction | null {
  const mine = action.requester === who.key;
  const may_decide = !mine && can(who, "refunds.approve") && action.status === "proposed";
  const carriesOut = mine && (action.status === "proposed" || action.status === "approved");
  if (action.approval_kind !== "supervisor" || (!may_decide && !carriesOut)) return null;
  return {
    action_id: action.action_id,
    tool: action.tool,
    status: action.status,
    approval_kind: action.approval_kind,
    summary: "Full refund of an order the carrier confirmed lost.",
    args: action.args,
    args_hash: action.args_hash,
    policy_snapshot: { self_approval_cap_minor: 500_000 },
    requester_name: PEOPLE[action.requester].name,
    created_at: action.created_at,
    mine,
    may_decide,
  };
}

function investigationOf(who: Person, run: RunRec): Investigation {
  return {
    run_id: run.run_id,
    by_name: PEOPLE[run.owner].name,
    mine: run.owner === who.key,
    status: run.status,
    started_at: run.started_at,
    finished_at: run.finished_at,
    root_cause: run.result?.root_cause ?? null,
    actions: (run.result?.proposed_actions ?? []).map(({ tool, status }) => ({ tool, status })),
  };
}

export function workspace(who: Person, ticketId: number): Workspace {
  const one = ticketFor(who, ticketId);
  const customer = world.customers.get(one.customer_id);
  return {
    ticket: {
      ticket_id: one.ticket_id,
      order_id: one.order_id,
      customer_id: one.customer_id,
      status: one.status,
      priority: one.priority,
      queue: one.queue,
      summary: one.summary,
      escalated_by_name: one.escalated_by_name,
      escalated_at: one.escalated_at,
      recommendation: one.recommendation,
      version: one.version,
      refund: one.refund,
      notes: one.notes,
    },
    order: world.orders.get(one.order_id) ?? null,
    customer: customer === undefined ? null : masked(who, customer),
    approvals: [...world.actions.values()]
      .filter((action) => action.ticket_id === ticketId)
      .map((action) => waitingFor(who, action))
      .filter((action) => action !== null),
    investigations: [...world.runs.values()]
      .filter((run) => run.ticket_id === ticketId)
      .reverse()
      .map((run) => investigationOf(who, run)),
  };
}

export function statusOf(who: Person, ticketId: number) {
  const one = ticketFor(who, ticketId);
  return {
    ticket_id: one.ticket_id,
    status: one.status,
    queue: one.queue,
    refund_state: one.refund?.state ?? null,
    version: one.version,
  };
}

export function resolve(who: Person, ticketId: number, click: string | null) {
  const one = ticketFor(who, ticketId);
  if (click === null || !/^[A-Za-z0-9_-]{8,56}$/.test(click)) {
    throw new Refusal("VALIDATION_FAILED", "The request does not match what this route accepts.");
  }
  if (!can(who, "tickets.write")) throw denied();
  if (inFlight(one)) {
    throw new Refusal("POLICY_VIOLATION", "A refund on this ticket is not finished yet.");
  }
  if (one.status !== "resolved") {
    one.status = "resolved";
    touch(one);
  }
  return { ticket_id: one.ticket_id, status: one.status, version: one.version };
}

function settle(one: TicketRec): void {
  if (one.refund === null) return;
  const failed = world.switches.fail_next_refund;
  world.switches.fail_next_refund = false;
  one.refund = {
    ...one.refund,
    state: failed ? "failed" : "paid",
    failure_reason: failed ? "The bank declined the transfer." : null,
  };
  one.notes.unshift({
    author_kind: "system",
    author_name: "Payments",
    body: failed ? "The refund payment failed." : "The refund was paid.",
    created_at: new Date().toISOString(),
  });
  touch(one);
}

/** What the payments worker does after an approval: sent, then paid or failed. */
export function handToPayments(one: TicketRec): void {
  const step = world.switches.payment_step_ms;
  later(step, () => {
    if (one.refund === null) return;
    one.refund = { ...one.refund, state: "sent_to_payments" };
    touch(one);
    later(step, () => settle(one));
  });
}

export function retryRefund(who: Person, refundId: number) {
  engineer(who);
  if (!can(who, "refunds.request")) throw denied();
  const one = [...world.tickets.values()].find(
    (candidate) => candidate.refund?.refund_id === refundId && sees(who, candidate),
  );
  if (one === undefined || one.refund === null) throw notFound("No refund with that number.");
  if (one.refund.state !== "failed") {
    throw new Refusal("POLICY_VIOLATION", "Only a failed payment can be sent again.");
  }
  one.refund = { ...one.refund, state: "approved", failure_reason: null };
  touch(one);
  handToPayments(one);
  return { refund_id: refundId, state: one.refund.state };
}

/** The order as this person may see it: null for another region's, as for one that does not exist. */
export function visibleOrder(who: Person, orderId: number) {
  const one = [...world.tickets.values()].find((candidate) => candidate.order_id === orderId);
  if (one === undefined || !sees(who, one) || !can(who, "orders.read")) return null;
  return world.orders.get(orderId) ?? null;
}

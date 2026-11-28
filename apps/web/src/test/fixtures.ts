/** The fictional people, ticket, order and refund every test of the web application uses. */
import type { ActionRow } from "@/lib/conversation";
import type {
  Customer,
  Me,
  Order,
  Refund,
  Ticket,
  TicketSummary,
  WaitingAction,
  Workspace,
} from "@/lib/schemas";
import type { UiLimits } from "@/lib/session";

export const TICKET_ID = 2207;
export const ORDER_ID = 4471;
export const REFUND_MINOR = 62_000;
export const REFUND_WORDS = "INR 620.00";
export const NBSP = "\u00a0";

/** Amounts are written with a space that does not break; tests compare with a plain one. */
export function plainSpaces(text: string): string {
  return text.replaceAll(NBSP, " ");
}
export const RUN_ID = "0a0a0a0a-0a0a-4a0a-8a0a-0a0a0a0a0a0a";
export const ACTION_ID = "1b1b1b1b-1b1b-4b1b-8b1b-1b1b1b1b1b1b";
export const OTHER_ACTION_ID = "2c2c2c2c-2c2c-4c2c-8c2c-2c2c2c2c2c2c";
export const ARGS_HASH = "ab".repeat(32);
export const NOW = new Date("2026-11-28T10:00:00Z");

export const LIMITS: UiLimits = {
  inboxRefreshMs: 15_000,
  refundPollMs: 3_000,
  refundPollMaxMs: 120_000,
  messageChars: 2_000,
  reasonChars: 300,
  runTokens: 60_000,
  runToolCalls: 12,
  retries: 1,
  retryWaitMs: 1_000,
  busyRetries: 3,
  busyWaitMs: 5_000,
  reconnectMaxMs: 15_000,
};

export const PRIYA: Me = {
  name: "Priya Nair",
  roles: ["Support.L1"],
  region: "west",
  permissions: ["tickets.read", "tickets.write"],
  demo_tools: false,
};

export const ARJUN: Me = {
  name: "Arjun Mehta",
  roles: ["Support.L2"],
  region: "west",
  permissions: ["tickets.read", "tickets.write", "refunds.request"],
  demo_tools: false,
};

export const KAVITA: Me = {
  name: "Kavita Rao",
  roles: ["Support.Supervisor", "Support.L2"],
  region: "*",
  permissions: ["tickets.read", "tickets.write", "refunds.request", "refunds.approve"],
  demo_tools: false,
};

/** The same person where the demonstration's Technical details switch is offered. */
export function withDemoTools(person: Me): Me {
  return { ...person, demo_tools: true };
}

export function ticketSummary(over: Partial<TicketSummary> = {}): TicketSummary {
  return {
    ticket_id: TICKET_ID,
    order_id: ORDER_ID,
    status: "open",
    priority: "P2",
    queue: "L1",
    summary: "Parcel marked lost, customer asks for a refund",
    customer_name: "Meera Pillai",
    customer_tier: "Gold",
    escalated_by_name: null,
    has_recommendation: false,
    refund_state: null,
    needs_my_approval: false,
    updated_at: "2026-11-28T09:48:00Z",
    ...over,
  };
}

export function refund(over: Partial<Refund> = {}): Refund {
  return {
    state: "approved",
    amount_minor: REFUND_MINOR,
    currency: "INR",
    reason_code: "lost_in_transit",
    requested_by_name: ARJUN.name,
    approved_by_name: ARJUN.name,
    failure_reason: null,
    refund_id: 31,
    ...over,
  };
}

export function ticket(over: Partial<Ticket> = {}): Ticket {
  return {
    ticket_id: TICKET_ID,
    order_id: ORDER_ID,
    customer_id: 118,
    status: "open",
    priority: "P2",
    queue: "L1",
    summary: "Parcel marked lost, customer asks for a refund",
    escalated_by_name: null,
    escalated_at: null,
    recommendation: null,
    version: 3,
    refund: null,
    notes: [],
    ...over,
  };
}

export function order(over: Partial<Order> = {}): Order {
  return {
    order_id: ORDER_ID,
    status: "shipped",
    status_age_hours: 96,
    is_stuck: true,
    amount_minor: REFUND_MINOR,
    currency: "INR",
    events: { recent: [{ type: "scan", summary: "Last scanned at the Pune hub" }] },
    ...over,
  };
}

export function customer(over: Partial<Customer> = {}): Customer {
  return {
    display_name: "Meera Pillai",
    tier: "Gold",
    email: "m•••@example.test",
    phone: null,
    contact_masked: true,
    ...over,
  };
}

export function workspace(over: Partial<Workspace> = {}): Workspace {
  return {
    ticket: ticket(),
    order: order(),
    customer: customer(),
    approvals: [],
    investigations: [],
    ...over,
  };
}

export function waitingAction(over: Partial<WaitingAction> = {}): WaitingAction {
  return {
    action_id: ACTION_ID,
    tool: "issue_refund",
    status: "pending",
    approval_kind: "supervisor",
    summary: "Refund on order 4471",
    args: {
      order_id: ORDER_ID,
      amount_minor: 620_000,
      reason_text: "The parcel was lost by the carrier.",
    },
    args_hash: ARGS_HASH,
    policy_snapshot: null,
    requester_name: ARJUN.name,
    created_at: "2026-11-28T09:30:00Z",
    mine: false,
    may_decide: true,
    ...over,
  };
}

export function refundRow(over: Partial<ActionRow> = {}): ActionRow {
  return {
    actionId: ACTION_ID,
    tool: "issue_refund",
    args: {
      order_id: ORDER_ID,
      amount_minor: REFUND_MINOR,
      reason_text: "The parcel was lost by the carrier.",
    },
    argsHash: ARGS_HASH,
    approvalKind: "self",
    status: "open",
    approver: null,
    ...over,
  };
}

export function noteRow(over: Partial<ActionRow> = {}): ActionRow {
  return {
    actionId: OTHER_ACTION_ID,
    tool: "update_ticket",
    args: { ticket_id: TICKET_ID, note: "Carrier confirmed the loss." },
    argsHash: ARGS_HASH,
    approvalKind: "self",
    status: "open",
    approver: null,
    ...over,
  };
}

/** Matches an accessible name that holds an amount, whichever space the amount is written with. */
export function named(text: string): (name: string) => boolean {
  return (name) => plainSpaces(name) === text;
}

/** Everything the fake agent remembers. One reset puts it back to the start of the story. */
import type { RunResult } from "@/lib/events";
import type { Customer, Note, Order, Priority, Refund } from "@/lib/schemas";
import type { UserKey } from "./people";

export type Recommendation = { amount_minor: number; reason_code: string; reason_text: string };

export type TicketRec = {
  ticket_id: number;
  order_id: number;
  customer_id: number;
  region: string;
  status: "open" | "resolved";
  priority: Priority;
  queue: "L1" | "L2";
  summary: string;
  escalated_by: UserKey | null;
  escalated_by_name: string | null;
  escalated_at: string | null;
  recommendation: Recommendation | null;
  version: number;
  refund: Refund | null;
  refund_requester: UserKey | null;
  notes: Note[];
  updated_at: string;
};

export type Json = Record<string, unknown>;
export type StoredEvent = { seq: number; type: string; at: string; data: Json };

export type ActionRec = {
  action_id: string;
  run_id: string;
  ticket_id: number;
  call_id: string;
  tool: "issue_refund" | "update_ticket";
  args: Json;
  args_hash: string;
  approval_kind: "self" | "supervisor";
  status: "proposed" | "approved" | "rejected" | "executed";
  requester: UserKey;
  approver: string | null;
  reason: string | null;
  created_at: string;
};

export type Beat =
  "wait" | "reauth" | { type: string; data: Json; effect?: () => void; result?: RunResult };

export type RunRec = {
  run_id: string;
  owner: UserKey;
  ticket_id: number | null;
  order_id: number | null;
  message: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  result: RunResult | null;
  stop_reason: string | null;
  events: StoredEvent[];
  pending: Beat[];
  actions: string[];
  calls: Map<string, { tool: string; args: Json; result: string | null }>;
  listeners: Set<() => void>;
  timer: NodeJS.Timeout | null;
  sign_ins_at_pause: number;
};

export type Fault = {
  method: string;
  path: string;
  times: number;
  status: number | null;
  retry_after_s: number | null;
  delay_ms: number;
};

export type JournalEntry = {
  method: string;
  path: string;
  user: UserKey | null;
  last_event_id: string | null;
  idempotency_key: string | null;
};

export type Switches = {
  pace_ms: number;
  payment_step_ms: number;
  fail_next_refund: boolean;
  drop_stream_after: number | null;
  runbooks_down: boolean;
  pause_for_reauth: boolean;
};

export type World = {
  tickets: Map<number, TicketRec>;
  orders: Map<number, Order>;
  customers: Map<number, Customer>;
  runs: Map<string, RunRec>;
  actions: Map<string, ActionRec>;
  faults: Fault[];
  journal: JournalEntry[];
  switches: Switches;
  timers: Set<NodeJS.Timeout>;
  next_refund_id: number;
  sign_ins: Map<UserKey, number>;
};

const MS_PER_HOUR = 3_600_000;
const DEFAULT_PACE_MS = 25;
const DEFAULT_PAYMENT_STEP_MS = 2000;
const FIRST_REFUND_ID = 9001;

export const TICKETS = { stuck: 2207, exception: 2188, lost: 2199, resolved: 2150 } as const;
export const ORDERS = { stuck: 4471, exception: 4502, lost: 3912, resolved: 4533 } as const;

function hoursAgo(hours: number): string {
  return new Date(Date.now() - hours * MS_PER_HOUR).toISOString();
}

function customerNote(name: string, body: string, hours: number): Note {
  return { author_kind: "customer", author_name: name, body, created_at: hoursAgo(hours) };
}

type Seed = Pick<
  TicketRec,
  "ticket_id" | "order_id" | "customer_id" | "priority" | "queue" | "summary" | "notes"
> & { status?: TicketRec["status"]; age_hours: number };

function ticket(seed: Seed): TicketRec {
  const { age_hours, ...rest } = seed;
  return {
    region: "west",
    status: "open",
    escalated_by: null,
    escalated_by_name: null,
    escalated_at: null,
    recommendation: null,
    version: 1,
    refund: null,
    refund_requester: null,
    updated_at: hoursAgo(age_hours),
    ...rest,
  };
}

function seededTickets(): TicketRec[] {
  return [
    ticket({
      ticket_id: TICKETS.stuck,
      order_id: ORDERS.stuck,
      customer_id: 501,
      priority: "P2",
      queue: "L1",
      summary: "Order stuck for four days",
      age_hours: 8,
      notes: [
        customerNote("Ananya Kulkarni", "This is the second time I am writing.", 8),
        customerNote("Ananya Kulkarni", "My order has not moved for days. Can someone check?", 60),
      ],
    }),
    ticket({
      ticket_id: TICKETS.exception,
      order_id: ORDERS.exception,
      customer_id: 502,
      priority: "P3",
      queue: "L1",
      summary: "Tracking shows a carrier exception",
      age_hours: 40,
      notes: [customerNote("Vikram Shah", "The tracking page says exception.", 40)],
    }),
    ticket({
      ticket_id: TICKETS.lost,
      order_id: ORDERS.lost,
      customer_id: 503,
      priority: "P2",
      queue: "L2",
      summary: "Parcel lost by carrier",
      age_hours: 36,
      notes: [customerNote("Deepa Reddy", "The courier says they cannot find it.", 36)],
    }),
    ticket({
      ticket_id: TICKETS.resolved,
      order_id: ORDERS.resolved,
      customer_id: 504,
      priority: "P3",
      queue: "L1",
      summary: "Paid but not shipped",
      status: "resolved",
      age_hours: 72,
      notes: [customerNote("Meera Joshi", "I paid yesterday and nothing has happened.", 96)],
    }),
  ];
}

function order(
  order_id: number,
  status: string,
  status_age_hours: number,
  amount_minor: number,
  last: { type: string; summary: string },
): Order {
  const is_stuck = status !== "delivered" && status !== "lost";
  return {
    order_id,
    status,
    status_age_hours,
    is_stuck,
    amount_minor,
    currency: "INR",
    events: { recent: [last] },
  };
}

function seededOrders(): Order[] {
  return [
    order(ORDERS.stuck, "packed", 96, 620_000, {
      type: "address_validation_failed",
      summary: "Address validation failed (postcode mismatch)",
    }),
    order(ORDERS.exception, "shipped", 104, 380_000, {
      type: "carrier_exception",
      summary: "Carrier exception (hub misroute)",
    }),
    order(ORDERS.lost, "lost", 40, 890_000, {
      type: "lost_in_transit_confirmed",
      summary: "Loss confirmed by Deccan Couriers",
    }),
    order(ORDERS.resolved, "delivered", 20, 145_000, {
      type: "delivered",
      summary: "Delivered",
    }),
  ];
}

function customer(display_name: string, tier: string, email: string, phone: string): Customer {
  return { display_name, tier, email, phone, contact_masked: false };
}

function seededCustomers(): [number, Customer][] {
  return [
    [501, customer("Ananya Kulkarni", "premium", "ananya.k@example.in", "+91 58200 11471")],
    [502, customer("Vikram Shah", "premium", "vikram.shah@example.in", "+91 58190 24502")],
    [503, customer("Deepa Reddy", "standard", "deepa.reddy@example.in", "+91 58330 53912")],
    [504, customer("Meera Joshi", "standard", "meera.joshi@example.in", "+91 58670 34533")],
  ];
}

function fresh(): World {
  return {
    tickets: new Map(seededTickets().map((one) => [one.ticket_id, one])),
    orders: new Map(seededOrders().map((one) => [one.order_id, one])),
    customers: new Map(seededCustomers()),
    runs: new Map(),
    actions: new Map(),
    faults: [],
    journal: [],
    switches: {
      pace_ms: DEFAULT_PACE_MS,
      payment_step_ms: DEFAULT_PAYMENT_STEP_MS,
      fail_next_refund: false,
      drop_stream_after: null,
      runbooks_down: false,
      pause_for_reauth: false,
    },
    timers: new Set(),
    next_refund_id: FIRST_REFUND_ID,
    sign_ins: new Map(),
  };
}

export let world: World = fresh();

/** Stops every clock of the old story before the new one starts, so no test hears another. */
export function reset(): void {
  for (const timer of world.timers) clearTimeout(timer);
  for (const run of world.runs.values()) {
    if (run.timer !== null) clearTimeout(run.timer);
    run.status = "cancelled";
    for (const listener of [...run.listeners]) listener();
  }
  world = fresh();
}

export function later(ms: number, what: () => void): void {
  const timer = setTimeout(() => {
    world.timers.delete(timer);
    what();
  }, ms);
  world.timers.add(timer);
}

export function touch(one: TicketRec): void {
  one.version += 1;
  one.updated_at = new Date().toISOString();
}

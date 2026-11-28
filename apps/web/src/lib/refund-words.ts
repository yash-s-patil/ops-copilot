import type { Tone } from "@/components/inbox/status-word";
import type { RefundState } from "@/lib/schemas";

/** How each refund state is said, and the tone of its dot. */
export const REFUND_WORDS: Record<RefundState, { words: string; tone: Tone }> = {
  waiting_for_supervisor: { words: "Refund waiting for supervisor", tone: "warn" },
  approved_by_supervisor: { words: "Refund approved by supervisor", tone: "accent" },
  approved: { words: "Refund approved", tone: "accent" },
  sent_to_payments: { words: "Refund sent to payments", tone: "warn" },
  paid: { words: "Refund paid", tone: "ok" },
  failed: { words: "Refund payment failed", tone: "bad" },
};

const IN_FLIGHT = new Set<RefundState>([
  "waiting_for_supervisor",
  "approved_by_supervisor",
  "approved",
  "sent_to_payments",
]);

const WITH_PAYMENTS = new Set<RefundState>(["approved", "sent_to_payments"]);
const WITH_SUPERVISOR = new Set<RefundState>(["waiting_for_supervisor", "approved_by_supervisor"]);

/** A supervisor has the refund: to decide it, or decided and not yet carried out. */
export function withSupervisor(state: RefundState | null): boolean {
  return state !== null && WITH_SUPERVISOR.has(state);
}

/** While a refund is not finished the ticket cannot be resolved. */
export function refundInFlight(state: RefundState | null): boolean {
  return state !== null && IN_FLIGHT.has(state);
}

/** While payment is under way the page keeps asking how it stands. */
export function paymentUnderWay(state: RefundState | null): boolean {
  return state !== null && WITH_PAYMENTS.has(state);
}

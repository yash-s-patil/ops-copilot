import { describe, expect, it } from "vitest";
import { paymentUnderWay, REFUND_WORDS, refundInFlight } from "@/lib/refund-words";
import type { RefundState } from "@/lib/schemas";

const STATES: readonly RefundState[] = [
  "waiting_for_supervisor",
  "approved_by_supervisor",
  "approved",
  "sent_to_payments",
  "paid",
  "failed",
];

describe("REFUND_WORDS", () => {
  it("has words that begin with Refund for every state", () => {
    expect(Object.keys(REFUND_WORDS).sort()).toEqual([...STATES].sort());
    for (const state of STATES) expect(REFUND_WORDS[state].words).toMatch(/^Refund /);
  });

  it("says paid in the good tone and failed in the bad one", () => {
    expect(REFUND_WORDS.paid).toEqual({ words: "Refund paid", tone: "ok" });
    expect(REFUND_WORDS.failed).toEqual({ words: "Refund payment failed", tone: "bad" });
  });

  it("uses the wording of the specification for a supervisor's approval", () => {
    expect(REFUND_WORDS.approved_by_supervisor.words).toBe("Refund approved by supervisor");
    expect(REFUND_WORDS.waiting_for_supervisor.words).toBe("Refund waiting for supervisor");
  });
});

describe("refundInFlight", () => {
  it.each([
    ["waiting_for_supervisor", true],
    ["approved_by_supervisor", true],
    ["approved", true],
    ["sent_to_payments", true],
    ["paid", false],
    ["failed", false],
  ] as const)("is %s → %s", (state, expected) => {
    expect(refundInFlight(state)).toBe(expected);
  });

  it("is false when there is no refund", () => {
    expect(refundInFlight(null)).toBe(false);
  });
});

describe("paymentUnderWay", () => {
  it.each([
    ["waiting_for_supervisor", false],
    ["approved_by_supervisor", false],
    ["approved", true],
    ["sent_to_payments", true],
    ["paid", false],
    ["failed", false],
  ] as const)("is %s → %s", (state, expected) => {
    expect(paymentUnderWay(state)).toBe(expected);
  });

  it("is false when there is no refund", () => {
    expect(paymentUnderWay(null)).toBe(false);
  });
});

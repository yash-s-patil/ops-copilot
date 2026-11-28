// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RefundProgress } from "@/components/thread/refund-progress";
import type { RefundState } from "@/lib/schemas";
import { violations } from "@/test/axe";

function drawn(state: RefundState) {
  const { container } = render(<RefundProgress state={state} />);
  const steps = screen.getAllByRole("listitem");
  return {
    container,
    labels: steps.map((step) => step.textContent),
    current: steps
      .filter((step) => step.getAttribute("aria-current") === "step")
      .map((step) => step.textContent),
  };
}

describe("RefundProgress", () => {
  it("is always three steps in order", () => {
    expect(drawn("approved").labels).toEqual(["Approved", "Sent to payments", "Paid"]);
  });

  it.each([
    ["approved", ["Sent to payments"]],
    ["sent_to_payments", ["Paid"]],
    ["paid", []],
    ["failed", []],
  ] as const)("when %s the current step is %j", (state, current) => {
    expect(drawn(state).current).toEqual(current);
  });

  it("ends in Payment failed, in the problem colour, when the payment failed", () => {
    const { labels, container } = drawn("failed");
    expect(labels).toEqual(["Approved", "Sent to payments", "Payment failed"]);
    expect(container.querySelector("li:last-child")?.className).toContain("text-bad");
  });

  it("says each state in words, so that colour is never the only sign", () => {
    for (const state of ["approved", "sent_to_payments", "paid", "failed"] as const) {
      expect(drawn(state).labels.every((label) => label !== "")).toBe(true);
    }
  });

  it("breaks no accessibility rule", async () => {
    expect(await violations(drawn("sent_to_payments").container)).toEqual([]);
  });
});

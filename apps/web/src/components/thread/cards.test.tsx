// @vitest-environment jsdom
import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  DecisionLine,
  RefundCard,
  ReplyDraft,
  StoppedCard,
  SummaryCard,
} from "@/components/thread/cards";
import { internalWords, violations } from "@/test/axe";
import { ARJUN, named, refund, REFUND_WORDS } from "@/test/fixtures";

afterEach(() => {
  vi.useRealTimers();
});

describe("RefundCard", () => {
  it("is not drawn when there is no refund", () => {
    const { container } = render(<RefundCard refund={null} />);
    expect(container.innerHTML).toBe("");
  });

  it.each(["waiting_for_supervisor", "approved_by_supervisor"] as const)(
    "is not drawn while the refund is %s, before it reaches payments",
    (state) => {
      const { container } = render(<RefundCard refund={refund({ state })} />);
      expect(container.innerHTML).toBe("");
    },
  );

  it.each([
    ["approved", "Sent to payments"],
    ["sent_to_payments", "Paid"],
  ] as const)("names the amount and marks the current step when %s", (state, current) => {
    render(<RefundCard refund={refund({ state })} />);
    expect(screen.getByRole("heading", { name: named(`Refund of ${REFUND_WORDS}`) })).toBeTruthy();
    const steps = screen.getAllByRole("listitem");
    expect(steps.map((step) => step.textContent)).toEqual(["Approved", "Sent to payments", "Paid"]);
    expect(steps.find((step) => step.getAttribute("aria-current") === "step")?.textContent).toBe(
      current,
    );
  });

  it("has no current step once the refund is paid", () => {
    const { container } = render(<RefundCard refund={refund({ state: "paid" })} />);
    expect(container.querySelector("[aria-current]")).toBeNull();
    expect(screen.getByText("Paid")).toBeTruthy();
  });

  it("says the payment failed in place of Paid", () => {
    render(<RefundCard refund={refund({ state: "failed", failure_reason: "declined" })} />);
    expect(screen.getByText("Payment failed")).toBeTruthy();
    expect(screen.queryByText("Paid")).toBeNull();
  });

  it("says that payment is handled elsewhere and the status follows by itself", () => {
    render(<RefundCard refund={refund()} />);
    expect(
      screen.getByText(
        "Payment is handled by the payments service after approval. This status updates by itself.",
      ),
    ).toBeTruthy();
  });

  it("writes the amount in the refund's own currency", () => {
    render(<RefundCard refund={refund({ amount_minor: 1_050, currency: "USD" })} />);
    expect(screen.getByRole("heading", { name: named("Refund of USD 10.50") })).toBeTruthy();
  });
});

describe("SummaryCard", () => {
  const terms = () => screen.getAllByRole("term").map((term) => term.textContent);

  it("gives the cause, what was done as sentences, and the notes", () => {
    const done = ["Refund approved and handed to payments", "Note added to the ticket"];
    render(<SummaryCard cause="The carrier lost the parcel." done={done} notes={["a", "b"]} />);
    expect(terms()).toEqual(["Cause", "Done", "Note"]);
    expect(screen.getByText("The carrier lost the parcel.")).toBeTruthy();
    expect(
      screen.getByText("Refund approved and handed to payments. Note added to the ticket."),
    ).toBeTruthy();
    expect(screen.getByText("a b")).toBeTruthy();
  });

  it("says nothing was changed when nothing was, and leaves out an empty note", () => {
    render(<SummaryCard cause="The parcel is in transit." done={[]} notes={[]} />);
    expect(screen.getByText("Nothing was changed.")).toBeTruthy();
    expect(terms()).toEqual(["Cause", "Done"]);
  });

  it("ends a single thing done with one full stop", () => {
    render(<SummaryCard cause="" done={["Note added to the ticket"]} notes={[]} />);
    expect(screen.getByText("Note added to the ticket.")).toBeTruthy();
  });
});

describe("ReplyDraft", () => {
  const DRAFT = "Dear Meera,\nwe have refunded order 4471.";

  function clipboard() {
    const writeText = vi.fn<(text: string) => Promise<void>>().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    return writeText;
  }

  it("shows the draft as written and says the engineer sends it", () => {
    render(<ReplyDraft draft={DRAFT} />);
    expect(screen.getByRole("heading", { name: "Reply to the customer" })).toBeTruthy();
    expect(screen.getByText("A draft. You send it yourself.")).toBeTruthy();
    expect(screen.getByText(/we have refunded order 4471/).textContent).toBe(DRAFT);
  });

  it("copies the whole draft and says so, to the eye and to a screen reader", async () => {
    const writeText = clipboard();
    render(<ReplyDraft draft={DRAFT} />);
    expect(screen.getByRole("status").textContent).toBe("");
    screen.getByRole("button", { name: "Copy draft" }).click();
    expect(await screen.findByRole("button", { name: "Copied" })).toBeTruthy();
    expect(writeText).toHaveBeenCalledWith(DRAFT);
    expect(screen.getByRole("status").textContent).toBe("Draft copied");
  });

  it("offers to copy again after a moment", async () => {
    vi.useFakeTimers();
    clipboard();
    render(<ReplyDraft draft={DRAFT} />);
    screen.getByRole("button", { name: "Copy draft" }).click();
    await act(() => vi.advanceTimersByTimeAsync(1_999));
    expect(screen.getByRole("button").textContent).toBe("Copied");
    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(screen.getByRole("button").textContent).toBe("Copy draft");
    expect(screen.getByRole("status").textContent).toBe("");
  });

  it("says so, rather than failing silently, when the browser refuses to copy", async () => {
    const writeText = vi.fn<(text: string) => Promise<void>>().mockRejectedValue(new Error("no"));
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    render(<ReplyDraft draft={DRAFT} />);
    screen.getByRole("button", { name: "Copy draft" }).click();
    expect(await screen.findByRole("button", { name: "Could not copy" })).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe(
      "The draft could not be copied. Select the text instead.",
    );
  });
});

describe("StoppedCard and DecisionLine", () => {
  it("gives the title as a heading and the reason below it", () => {
    render(<StoppedCard title="Investigation stopped" reason="You stopped this investigation." />);
    expect(screen.getByRole("heading", { name: "Investigation stopped" })).toBeTruthy();
    expect(screen.getByText("You stopped this investigation.")).toBeTruthy();
  });

  it("draws a stopped card with no reason", () => {
    render(<StoppedCard title="Investigation stopped" reason="" />);
    expect(screen.getByRole("heading")).toBeTruthy();
  });

  it("says who approved or who rejected", () => {
    render(<DecisionLine approved approver={ARJUN.name} />);
    render(<DecisionLine approved={false} approver="Kavita Rao" />);
    expect(screen.getByText("Approved by Arjun Mehta")).toBeTruthy();
    expect(screen.getByText("Rejected by Kavita Rao")).toBeTruthy();
  });
});

describe("the cards together", () => {
  it("break no accessibility rule and use no internal words", async () => {
    const { container } = render(
      <div>
        <RefundCard refund={refund({ state: "sent_to_payments" })} />
        <SummaryCard cause="Lost." done={[]} notes={["Check with the carrier."]} />
        <ReplyDraft draft="We have refunded your order." />
        <StoppedCard title="Investigation stopped" reason="It reached its limit." />
        <DecisionLine approved approver={ARJUN.name} />
      </div>,
    );
    expect(await violations(container)).toEqual([]);
    expect(internalWords(container)).toEqual([]);
  });
});

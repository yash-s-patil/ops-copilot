// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TicketRow } from "@/components/inbox/ticket-row";
import type { TicketSummary } from "@/lib/schemas";
import { internalWords, violations } from "@/test/axe";
import { NOW, ticketSummary } from "@/test/fixtures";

type Options = { selected?: boolean; awaitingMe?: boolean };

function row(
  over: Partial<TicketSummary> = {},
  { selected = false, awaitingMe = false }: Options = {},
) {
  const { container } = render(
    <TicketRow
      ticket={ticketSummary(over)}
      view="my_queue"
      selected={selected}
      awaitingMe={awaitingMe}
      now={NOW}
    />,
  );
  return { container, link: within(container).getByRole("link") };
}

/** The state words of a row, which are the ones drawn with a dot before them. */
function stateWords(container: HTMLElement): (string | null)[] {
  return [...container.querySelectorAll("span.inline-flex")].map((word) => word.textContent);
}

describe("a ticket in the list", () => {
  it("says what it is about, how old it is, and which ticket, order and customer", () => {
    const { link } = row();
    expect(link.textContent).toContain("Parcel marked lost, customer asks for a refund");
    expect(link.textContent).toContain("12 min");
    expect(link.textContent).toContain("#2207 · Order 4471 · Meera Pillai · Gold");
    expect(screen.getByRole("img", { name: "Priority P2" })).toBeTruthy();
  });

  it("leads to the ticket and keeps the view it was listed in", () => {
    expect(row().link.getAttribute("href")).toBe("/inbox/2207?view=my_queue");
  });

  it("is marked as the current one only when it is selected", () => {
    expect(row().link.hasAttribute("aria-current")).toBe(false);
    expect(row({}, { selected: true }).link.getAttribute("aria-current")).toBe("true");
  });

  it("has no state words when nothing is going on", () => {
    expect(stateWords(row().container)).toEqual([]);
  });
});

describe("the state words of a ticket", () => {
  it.each([
    ["waiting_for_supervisor", "Refund waiting for supervisor"],
    ["approved_by_supervisor", "Refund approved by supervisor"],
    ["approved", "Refund approved"],
    ["sent_to_payments", "Refund sent to payments"],
    ["paid", "Refund paid"],
    ["failed", "Refund payment failed"],
  ] as const)("say where a refund is when it is %s", (refund_state, words) => {
    expect(stateWords(row({ refund_state }).container)).toEqual([words]);
  });

  it("say a refund is recommended, until there is a refund to speak of", () => {
    expect(stateWords(row({ has_recommendation: true }).container)).toEqual(["Refund recommended"]);
    const both = row({ has_recommendation: true, refund_state: "paid" });
    expect(stateWords(both.container)).toEqual(["Refund paid"]);
  });

  it("say who escalated, by first name", () => {
    const escalated = row({ escalated_by_name: "Priya Nair" });
    expect(stateWords(escalated.container)).toEqual(["Escalated by Priya"]);
    const oneName = row({ escalated_by_name: "Priya" });
    expect(stateWords(oneName.container)).toEqual(["Escalated by Priya"]);
  });

  it("say it needs the viewer's approval, as a supervisor or as the one who asked", () => {
    expect(stateWords(row({ needs_my_approval: true }).container)).toEqual(["Needs your approval"]);
    expect(stateWords(row({}, { awaitingMe: true }).container)).toEqual(["Needs your approval"]);
  });

  it("say it once when both are true, after the refund and the escalation", () => {
    const { container } = row(
      {
        refund_state: "waiting_for_supervisor",
        escalated_by_name: "Priya Nair",
        needs_my_approval: true,
      },
      { awaitingMe: true },
    );
    expect(stateWords(container)).toEqual([
      "Refund waiting for supervisor",
      "Escalated by Priya",
      "Needs your approval",
    ]);
  });
});

describe("the row", () => {
  it("breaks no accessibility rule and uses no internal words", async () => {
    const { container } = row(
      { refund_state: "failed", needs_my_approval: true },
      { selected: true },
    );
    expect(await violations(container)).toEqual([]);
    expect(internalWords(container)).toEqual([]);
  });
});

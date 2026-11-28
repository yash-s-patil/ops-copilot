// @vitest-environment jsdom
import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DetailsPane } from "@/components/details/details-pane";
import type { Me, Workspace } from "@/lib/schemas";
import { internalWords, violations } from "@/test/axe";
import { answerJson, fakeFetch, sent, type FetchMock } from "@/test/fetch";
import { ARJUN, customer, KAVITA, order, PRIYA, refund, ticket, workspace } from "@/test/fixtures";
import { renderAs } from "@/test/render";

let fetchMock: FetchMock;

beforeEach(() => {
  fetchMock = fakeFetch();
  answerJson(fetchMock, {});
});

function pane(shown: Workspace = workspace(), me: Me = PRIYA) {
  const onClose = vi.fn<() => void>();
  const rendered = renderAs(<DetailsPane workspace={shown} onClose={onClose} />, me);
  const said = (rendered.subject.textContent ?? "").replaceAll(" ", " ");
  return { ...rendered, onClose, said };
}

/** One of the pane's sections, found by its heading. */
function section(subject: HTMLElement, label: string): HTMLElement {
  const heading = within(subject).getByRole("heading", { name: label });
  if (heading.parentElement === null) throw new Error(`no section called ${label}`);
  return heading.parentElement;
}

const retry = () => screen.queryByRole("button", { name: "Retry payment" });

describe("the customer", () => {
  it("is shown with a line saying contact details are masked for an L1", () => {
    const { subject, said } = pane(workspace(), PRIYA);
    expect(section(subject, "Customer").textContent).toContain("Meera Pillai · Gold");
    expect(said).toContain("Contact details are masked for L1.");
    expect(said).toContain("Phone" + "Not given");
  });

  it("is shown in full, with no line about masking, to someone who may see it", () => {
    const full = customer({
      email: "meera@example.test",
      phone: "+91 00000 00000",
      contact_masked: false,
    });
    const { said } = pane(workspace({ customer: full }), KAVITA);
    expect(said).toContain("meera@example.test");
    expect(said).toContain("+91 00000 00000");
    expect(said).not.toContain("masked");
  });

  it("is said to be unavailable when it could not be read", () => {
    const { subject } = pane(workspace({ customer: null }));
    expect(section(subject, "Customer").textContent).toContain("The customer could not be loaded.");
  });
});

describe("the order", () => {
  it("is shown with its stage, how long it has been there, its amount and its last event", () => {
    const { subject, said } = pane();
    const shown = section(subject, "Order 4471");
    expect(within(shown).getAllByRole("listitem")).toHaveLength(5);
    expect(said).toContain("Status" + "Shipped for 96 hours");
    expect(said).toContain("Amount" + "INR 620.00");
    expect(said).toContain("Last event" + "Last scanned at the Pune hub");
  });

  it("says the carrier confirmed the loss of a lost order", () => {
    expect(pane(workspace({ order: order({ status: "lost" }) })).said).toContain(
      "Loss confirmed by carrier",
    );
  });

  it.each([
    ["no events were read", null],
    ["there are no events", { recent: [] }],
  ])("says none is recorded when %s", (_what, events) => {
    expect(pane(workspace({ order: order({ events }) })).said).toContain(
      "Last event" + "None recorded",
    );
  });

  it("is named and said to be unavailable when it could not be read", () => {
    const { subject } = pane(workspace({ order: null }));
    expect(section(subject, "Order 4471").textContent).toContain("The order could not be loaded.");
  });

  it("counts one hour as one hour", () => {
    const young = order({ status: "paid", status_age_hours: 1 });
    expect(pane(workspace({ order: young })).said).toContain("Paid for 1 hour" + "Amount");
  });
});

const withRefund = (over: Parameters<typeof refund>[0]) =>
  workspace({ ticket: ticket({ refund: refund(over) }) });

describe("the refund", () => {
  it("is said not to exist when there is none and none was recommended", () => {
    expect(pane().said).toContain("No refund on this order.");
  });

  it("is shown as a recommendation, with who made it, on an escalated ticket", () => {
    const recommendation = { amount_minor: 62_000, reason_code: "lost", reason_text: "Lost." };
    const escalated = ticket({ escalated_by_name: PRIYA.name, recommendation });
    expect(pane(workspace({ ticket: escalated }), ARJUN).said).toContain(
      "Recommended by Priya Nair: INR 620.00.",
    );
  });

  it("is shown with its amount and state, and no progress, before it reaches payments", () => {
    const { subject, said } = pane(withRefund({ state: "waiting_for_supervisor" }), ARJUN);
    expect(said).toContain("INR 620.00" + "waiting for supervisor");
    expect(within(section(subject, "Refund")).queryAllByRole("listitem")).toEqual([]);
  });

  it.each(["approved", "sent_to_payments", "paid", "failed"] as const)(
    "is shown with its three steps once it is %s",
    (state) => {
      const { subject } = pane(withRefund({ state }), ARJUN);
      expect(within(section(subject, "Refund")).getAllByRole("listitem")).toHaveLength(3);
    },
  );
});

describe("retrying a failed payment", () => {
  it("offers Retry payment when the payment failed and the viewer may ask for refunds", () => {
    pane(withRefund({ state: "failed" }), ARJUN);
    expect(retry()).not.toBeNull();
  });

  it("does not offer it to someone who may not ask for refunds", () => {
    pane(withRefund({ state: "failed" }), PRIYA);
    expect(retry()).toBeNull();
  });

  it.each(["approved", "sent_to_payments", "paid"] as const)(
    "does not offer it while the refund is %s",
    (state) => {
      pane(withRefund({ state }), ARJUN);
      expect(retry()).toBeNull();
    },
  );

  it("does not offer it for a failed refund that has no number to retry", () => {
    pane(withRefund({ state: "failed", refund_id: null }), ARJUN);
    expect(retry()).toBeNull();
  });

  it("asks this application's server to retry that refund, once, on a click", async () => {
    const { user } = pane(withRefund({ state: "failed" }), ARJUN);
    await user.click(screen.getByRole("button", { name: "Retry payment" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(sent(fetchMock)).toMatchObject({ url: "/api/bff/refunds/31/retry", method: "POST" });
  });

  it("says what went wrong when the retry is refused", async () => {
    const refusal = { code: "BACKEND_UNAVAILABLE", message: "Payments are not answering." };
    answerJson(fetchMock, refusal, { status: 503 });
    const { user } = pane(withRefund({ state: "failed" }), ARJUN);
    await user.click(screen.getByRole("button", { name: "Retry payment" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Payments are not answering.");
  });
});

describe("the ticket's activity", () => {
  it("says there are no notes yet when there are none", () => {
    expect(pane().said).toContain("No notes yet.");
  });

  it("names the author of each note, and says when the assistant wrote for someone", () => {
    const at = (minute: number) => `2026-11-28T09:0${minute}:00Z`;
    const notes = [
      {
        author_kind: "customer",
        author_name: "Meera Pillai",
        body: "Where is it?",
        created_at: at(1),
      },
      { author_kind: "engineer", author_name: "Priya Nair", body: "Checking.", created_at: at(2) },
      {
        author_kind: "agent_on_behalf",
        author_name: "Priya Nair",
        body: "Lost.",
        created_at: at(3),
      },
      { author_kind: "system", author_name: "System", body: "Escalated.", created_at: at(4) },
    ] as const;
    const { subject } = pane(workspace({ ticket: ticket({ notes: [...notes] }) }));
    const authors = [...section(subject, "Ticket activity").querySelectorAll("b")];
    expect(authors.map((author) => author.textContent)).toEqual([
      "Meera Pillai (customer)",
      "Priya Nair",
      "Copilot, for Priya Nair",
      "System",
    ]);
  });
});

describe("the pane", () => {
  it("closes when its close button is clicked", async () => {
    const { user, onClose } = pane();
    await user.click(screen.getByRole("button", { name: "Close details" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("breaks no accessibility rule and uses no internal words", async () => {
    const full = workspace({ ticket: ticket({ refund: refund({ state: "failed" }) }) });
    const { subject } = pane(full, ARJUN);
    expect(await violations(subject)).toEqual([]);
    expect(internalWords(subject)).toEqual([]);
  });
});

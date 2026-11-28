// @vitest-environment jsdom
import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TicketHeader } from "@/components/thread/ticket-header";
import type { Me, Ticket } from "@/lib/schemas";
import { internalWords, violations } from "@/test/axe";
import { answerJson, fakeFetch, sent, type FetchMock } from "@/test/fetch";
import { ARJUN, PRIYA, refund, ticket, TICKET_ID } from "@/test/fixtures";
import { renderAs } from "@/test/render";

let fetchMock: FetchMock;

beforeEach(() => {
  fetchMock = fakeFetch();
  answerJson(fetchMock, { ticket_id: TICKET_ID, status: "resolved", version: 4 });
});

function header(shown: Ticket = ticket(), me: Me = PRIYA) {
  const onDetails = vi.fn<() => void>();
  return {
    onDetails,
    ...renderAs(<TicketHeader ticket={shown} view="waiting" onDetails={onDetails} />, me),
  };
}

const resolveButton = () => screen.queryByRole("button", { name: "Resolve" });

describe("the header of a ticket", () => {
  it("names the ticket, its order, priority and queue", () => {
    header();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(ticket().summary);
    expect(screen.getByText("#2207 · Order 4471 · P2 · L1 queue")).toBeTruthy();
  });

  it("leads back to the list in the view the engineer came from", () => {
    header();
    expect(screen.getByRole("link", { name: "Back to list" }).getAttribute("href")).toBe(
      "/inbox?view=waiting",
    );
  });

  it("opens the details when its button is clicked", async () => {
    const { user, onDetails } = header();
    await user.click(screen.getByRole("button", { name: "Ticket details" }));
    expect(onDetails).toHaveBeenCalledTimes(1);
  });
});

describe("who may resolve", () => {
  it.each(["open", "pending_customer"])("offers Resolve on a ticket that is %s", (status) => {
    header(ticket({ status }));
    expect(resolveButton()).not.toBeNull();
  });

  it("says Resolved, and offers nothing, on a resolved ticket", () => {
    header(ticket({ status: "resolved" }));
    expect(resolveButton()).toBeNull();
    expect(screen.getByText("Resolved")).toBeTruthy();
  });

  it("offers nothing to someone who may not change tickets", () => {
    header(ticket(), { ...PRIYA, permissions: ["tickets.read"] });
    expect(resolveButton()).toBeNull();
  });

  it.each([
    "waiting_for_supervisor",
    "approved_by_supervisor",
    "approved",
    "sent_to_payments",
  ] as const)("offers nothing while a refund is %s", (state) => {
    header(ticket({ refund: refund({ state }) }), ARJUN);
    expect(resolveButton()).toBeNull();
  });

  it.each(["paid", "failed"] as const)("offers Resolve once the refund is %s", (state) => {
    header(ticket({ refund: refund({ state }) }), ARJUN);
    expect(resolveButton()).not.toBeNull();
  });
});

describe("resolving", () => {
  it("asks to confirm first, and changes nothing on the first click", async () => {
    const { user } = header();
    await user.click(screen.getByRole("button", { name: "Resolve" }));
    expect(screen.getByRole("button", { name: "Confirm resolve" })).toBeTruthy();
    expect(resolveButton()).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("resolves on the confirming click, with a key for that click, then goes back to Resolve", async () => {
    const { user } = header();
    await user.click(screen.getByRole("button", { name: "Resolve" }));
    await user.click(screen.getByRole("button", { name: "Confirm resolve" }));
    await waitFor(() => expect(resolveButton()).not.toBeNull());
    const request = sent(fetchMock);
    expect(request.url).toBe("/api/bff/tickets/2207/resolve");
    expect(request.method).toBe("POST");
    expect(request.headers.get("idempotency-key")).toMatch(/^[0-9a-f]{32}$/);
  });

  it("says what went wrong and tries again with the same key", async () => {
    answerJson(
      fetchMock,
      { code: "CONFLICT", message: "The ticket changed. Reload it." },
      { status: 409 },
    );
    const { user } = header();
    await user.click(screen.getByRole("button", { name: "Resolve" }));
    await user.click(screen.getByRole("button", { name: "Confirm resolve" }));
    expect((await screen.findByRole("alert")).textContent).toBe("The ticket changed. Reload it.");
    await user.click(screen.getByRole("button", { name: "Confirm resolve" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const key = (index: number) => sent(fetchMock, index).headers.get("idempotency-key");
    expect(key(1)).toBe(key(0));
  });
});

describe("resolving again", () => {
  it("uses a new key for the next resolve after one went through", async () => {
    const { user } = header();
    for (let round = 0; round < 2; round += 1) {
      await user.click(screen.getByRole("button", { name: "Resolve" }));
      await user.click(screen.getByRole("button", { name: "Confirm resolve" }));
      await waitFor(() => expect(resolveButton()).not.toBeNull());
    }
    const key = (index: number) => sent(fetchMock, index).headers.get("idempotency-key");
    expect(key(1)).not.toBe(key(0));
  });

  it("says it did not go through when the server cannot be reached", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const { user } = header();
    await user.click(screen.getByRole("button", { name: "Resolve" }));
    await user.click(screen.getByRole("button", { name: "Confirm resolve" }));
    expect((await screen.findByRole("alert")).textContent).toBe("That did not go through.");
  });
});

describe("the header", () => {
  it("breaks no accessibility rule and uses no internal words", async () => {
    const { subject, user } = header();
    expect(await violations(subject)).toEqual([]);
    await user.click(screen.getByRole("button", { name: "Resolve" }));
    expect(await violations(subject)).toEqual([]);
    expect(internalWords(subject)).toEqual([]);
  });
});

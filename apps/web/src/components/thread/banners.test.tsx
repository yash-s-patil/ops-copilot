// @vitest-environment jsdom
import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  CustomerMessage,
  EscalationBanner,
  PriorInvestigation,
  SupervisorApprovalBanner,
} from "@/components/thread/banners";
import { ApiError } from "@/lib/api";
import { formatClock } from "@/lib/format";
import type { Investigation, WaitingAction } from "@/lib/schemas";
import { internalWords, violations } from "@/test/axe";
import { ARJUN, KAVITA, PRIYA, RUN_ID, ticket, waitingAction } from "@/test/fixtures";
import { renderAs } from "@/test/render";

const said = (subject: HTMLElement) => (subject.textContent ?? "").replaceAll(" ", " ");

describe("EscalationBanner", () => {
  it("is not drawn for a ticket that was not escalated", () => {
    const { subject } = renderAs(<EscalationBanner ticket={ticket()} />);
    expect(subject.innerHTML).toBe("");
  });

  it("says who escalated, with no recommendation when none was made", () => {
    const { subject } = renderAs(
      <EscalationBanner ticket={ticket({ escalated_by_name: PRIYA.name })} />,
    );
    expect(said(subject)).toBe("Escalated to L2 by Priya Nair");
  });

  it("gives the recommended refund and its reason in plain words", () => {
    const recommendation = {
      amount_minor: 62_000,
      reason_code: "lost_in_transit",
      reason_text: "Lost by the carrier.",
    };
    const escalated = ticket({ escalated_by_name: PRIYA.name, recommendation });
    const { subject } = renderAs(<EscalationBanner ticket={escalated} />, ARJUN);
    expect(said(subject)).toContain("Recommended refund: INR 620.00 (lost in transit).");
    expect(said(subject)).not.toContain("lost_in_transit");
  });
});

describe("PriorInvestigation", () => {
  const run = (over: Partial<Investigation> = {}): Investigation => ({
    run_id: RUN_ID,
    by_name: PRIYA.name,
    mine: false,
    status: "completed",
    started_at: "2026-11-28T09:12:00Z",
    finished_at: "2026-11-28T09:13:00Z",
    root_cause: "The carrier lost the parcel.",
    actions: [],
    ...over,
  });

  it("says who looked and when, with the full time in UTC as a tooltip", () => {
    const { subject } = renderAs(<PriorInvestigation run={run()} />);
    const at = formatClock(new Date("2026-11-28T09:12:00Z"));
    expect(screen.getByText(`Investigation by Priya Nair · ${at}`)).toBeTruthy();
    expect(subject.querySelector("[title]")?.getAttribute("title")).toBe("2026-11-28 09:12 UTC");
  });

  it("gives the cause and what was done, without naming how it was done", () => {
    const actions = [
      { tool: "update_ticket", status: "executed" },
      { tool: "issue_refund", status: "executed" },
      { tool: "issue_refund", status: "rejected" },
      { tool: "close_account", status: "executed" },
      { tool: null, status: "executed" },
    ];
    const { subject } = renderAs(<PriorInvestigation run={run({ actions })} />);
    expect(said(subject)).toContain(
      "The carrier lost the parcel. Ticket updated. Refund approved and handed to payments. A change was made.",
    );
    expect(internalWords(subject)).toEqual([]);
    expect(said(subject)).not.toMatch(/update_ticket|issue_refund|close_account/);
  });

  it("says it is still in progress when it has found no cause yet", () => {
    const { subject } = renderAs(
      <PriorInvestigation run={run({ root_cause: null, status: "running", finished_at: null })} />,
    );
    expect(said(subject)).toContain("Still in progress.");
  });
});

describe("CustomerMessage", () => {
  it("shows what the customer wrote under their name", () => {
    renderAs(<CustomerMessage name="Meera Pillai" body="Where is my parcel?" />);
    expect(screen.getByText("Meera Pillai · customer")).toBeTruthy();
    expect(screen.queryByRole("heading")).toBeNull();
    expect(screen.getByText("Where is my parcel?")).toBeTruthy();
  });
});

function banner(action: WaitingAction) {
  const approve = vi.fn<() => Promise<unknown>>().mockResolvedValue(undefined);
  const reject = vi.fn<(reason: string) => Promise<unknown>>().mockResolvedValue(undefined);
  const rendered = renderAs(
    <SupervisorApprovalBanner action={action} approve={approve} reject={reject} />,
    KAVITA,
  );
  return { ...rendered, approve, reject };
}

describe("a refund waiting for a supervisor", () => {
  it("says what is asked, by whom and why, and offers the decision", () => {
    const { subject } = banner(waitingAction());
    expect(said(subject)).toContain("Refund of INR 6,200.00 needs your approval");
    expect(said(subject)).toContain("Requested by Arjun Mehta at ");
    expect(said(subject)).toContain("Policy check: The parcel was lost by the carrier.");
    expect(screen.getByRole("button", { name: "Approve refund" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reject" })).toBeTruthy();
  });

  it("falls back to the summary when the request names no reason", () => {
    const { subject } = banner(waitingAction({ args: {}, summary: "Refund on order 4471" }));
    expect(said(subject)).toContain("Policy check: Refund on order 4471");
  });

  it("still reads as a sentence when the request names no amount", () => {
    const { subject } = banner(waitingAction({ args: {} }));
    expect(said(subject)).not.toContain("Refund of a refund");
  });

  it("approves on one click", async () => {
    const { user, approve, reject } = banner(waitingAction());
    await user.click(screen.getByRole("button", { name: "Approve refund" }));
    expect(approve).toHaveBeenCalledTimes(1);
    expect(reject).not.toHaveBeenCalled();
  });

  it("rejects only with a reason", async () => {
    const { user, approve, reject } = banner(waitingAction());
    await user.click(screen.getByRole("button", { name: "Reject" }));
    await user.keyboard("{Enter}");
    expect(reject).not.toHaveBeenCalled();
    await user.type(screen.getByRole("textbox", { name: "Reason for rejecting" }), "Too much.");
    await user.click(screen.getByRole("button", { name: "Reject" }));
    expect(reject).toHaveBeenCalledWith("Too much.");
    expect(approve).not.toHaveBeenCalled();
  });
});

describe("a supervisor's decision that fails or is not theirs to make", () => {
  it("shows the server's words when the decision fails", async () => {
    const { user, approve } = banner(waitingAction());
    approve.mockRejectedValue(
      new ApiError(403, "PERMISSION_DENIED", "A different person must approve."),
    );
    await user.click(screen.getByRole("button", { name: "Approve refund" }));
    expect((await screen.findByRole("alert")).textContent).toBe("A different person must approve.");
  });

  it("shows plain words when the decision fails for a reason it cannot name", async () => {
    const { user, approve } = banner(waitingAction());
    approve.mockRejectedValue(new TypeError("Failed to fetch"));
    await user.click(screen.getByRole("button", { name: "Approve refund" }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "That did not go through. Try again.",
    );
  });

  it("tells the requester they cannot approve their own request, and offers no decision", () => {
    const { subject } = banner(
      waitingAction({ mine: true, may_decide: false, requester_name: KAVITA.name }),
    );
    expect(said(subject)).toContain("You cannot approve your own request.");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("offers no decision to someone who may not decide, without saying it is their own", () => {
    const { subject } = banner(waitingAction({ may_decide: false }));
    expect(screen.queryByRole("button")).toBeNull();
    expect(said(subject)).not.toContain("You cannot approve your own request.");
  });
});

describe("a refund a supervisor has approved", () => {
  it("says so, says when it completes, and offers nothing more to decide", () => {
    const { subject } = banner(waitingAction({ status: "approved" }));
    expect(said(subject)).toContain("You approved a refund of INR 6,200.00");
    expect(said(subject)).toContain("It completes when Arjun next opens the ticket.");
    expect(said(subject)).not.toContain("needs your approval");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("no longer says the request is the viewer's own", () => {
    const { subject } = banner(waitingAction({ status: "approved", mine: true }));
    expect(said(subject)).not.toContain("You cannot approve your own request.");
  });
});

describe("the banners", () => {
  it("break no accessibility rule and use no internal words", async () => {
    const waiting = banner(waitingAction());
    expect(await violations(waiting.subject)).toEqual([]);
    expect(internalWords(waiting.subject)).toEqual([]);
    const approved = banner(waitingAction({ status: "approved" }));
    expect(internalWords(approved.subject)).toEqual([]);
    const escalated = renderAs(
      <EscalationBanner ticket={ticket({ escalated_by_name: PRIYA.name })} />,
    );
    expect(await violations(escalated.subject)).toEqual([]);
  });
});

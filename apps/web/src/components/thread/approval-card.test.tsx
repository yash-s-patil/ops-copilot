// @vitest-environment jsdom
import { screen, waitFor, within } from "@testing-library/react";
import { describe as group, expect, it, vi } from "vitest";
import { ApprovalCard, describe } from "@/components/thread/approval-card";
import { ApiError } from "@/lib/api";
import type { ActionRow } from "@/lib/conversation";
import { internalWords, violations } from "@/test/axe";
import {
  ACTION_ID,
  ARGS_HASH,
  ARJUN,
  noteRow,
  plainSpaces,
  refundRow,
  REFUND_WORDS,
  TICKET_ID,
  withDemoTools,
} from "@/test/fixtures";
import { renderAs, switchTechnicalOn } from "@/test/render";

function decisions() {
  return {
    approve: vi.fn<(action: ActionRow) => Promise<unknown>>().mockResolvedValue(undefined),
    reject: vi
      .fn<(action: ActionRow, reason: string) => Promise<unknown>>()
      .mockResolvedValue(undefined),
  };
}

const BOTH = [noteRow(), refundRow()];

function openCard(actions: readonly ActionRow[] = BOTH, decide = decisions()) {
  return { decide, ...renderAs(<ApprovalCard actions={actions} open decide={decide} />, ARJUN) };
}

group("what a change is called", () => {
  const words = (row: ActionRow) => {
    const { title, detail } = describe(row);
    return { title: plainSpaces(title), detail: plainSpaces(detail) };
  };

  it("names a refund by its amount and order, with the reason and who may approve", () => {
    expect(words(refundRow())).toEqual({
      title: `Refund ${REFUND_WORDS} on order 4471`,
      detail:
        "The parcel was lost by the carrier. You can approve refunds up to INR 5,000 yourself.",
    });
  });

  it("says a supervisor must approve a refund that needs one", () => {
    expect(words(refundRow({ approvalKind: "supervisor" })).detail).toContain(
      "Above INR 5,000, so a supervisor must approve.",
    );
  });

  it("copes with a refund that names no amount, order or reason", () => {
    const bare = words(refundRow({ args: {} }));
    expect(bare.title).toMatch(/^Refund\b/);
    expect(bare.detail).toBe("You can approve refunds up to INR 5,000 yourself.");
  });

  it("names a note by its ticket and quotes it", () => {
    expect(words(noteRow())).toEqual({
      title: `Add a note to ticket #${TICKET_ID}`,
      detail: '"Carrier confirmed the loss."',
    });
  });

  it("says so when a note also escalates, or changes the ticket's status", () => {
    const args = { ticket_id: TICKET_ID, note: "Needs a refund." };
    expect(words(noteRow({ args: { ...args, escalate_to: "L2" } })).title).toBe(
      `Add a note and escalate ticket #${TICKET_ID} to L2`,
    );
    expect(words(noteRow({ args: { ...args, status: "resolved" } })).title).toBe(
      `Mark ticket #${TICKET_ID} as resolved`,
    );
  });

  it("has no detail for a change with no note", () => {
    expect(words(noteRow({ args: { ticket_id: TICKET_ID } })).detail).toBe("");
  });
});

group("an open card", () => {
  it("lists each change in words and says nothing has been changed yet", () => {
    openCard();
    const card = screen.getByRole("group", { name: "Approve these changes" });
    expect(within(card).getAllByRole("listitem")).toHaveLength(2);
    expect(within(card).getByText(`Add a note to ticket #${TICKET_ID}`)).toBeTruthy();
    expect(within(card).getByText(`Refund ${REFUND_WORDS} on order 4471`)).toBeTruthy();
    expect(within(card).getByText("Nothing has been changed yet.")).toBeTruthy();
  });

  it("takes the focus when it appears", () => {
    openCard();
    expect(document.activeElement).toBe(screen.getByRole("group"));
  });

  it("approves each open change, one after another, when Approve is clicked", async () => {
    const { decide, user } = openCard();
    await user.click(screen.getByRole("button", { name: "Approve" }));
    expect(decide.approve.mock.calls.map(([action]) => action.actionId)).toEqual(
      BOTH.map((row) => row.actionId),
    );
    expect(decide.reject).not.toHaveBeenCalled();
  });

  it("decides only the changes that are still open", async () => {
    const rows = [noteRow({ status: "approved" }), refundRow(), refundRow({ status: "waiting" })];
    const { decide, user } = openCard(rows);
    await user.click(screen.getByRole("button", { name: "Approve" }));
    expect(decide.approve).toHaveBeenCalledTimes(1);
    expect(decide.approve).toHaveBeenCalledWith(rows[1]);
  });
});

group("an approval that is slow or fails", () => {
  it("cannot be approved twice while the first approval is under way", async () => {
    const decide = decisions();
    let finish: (value: unknown) => void = () => undefined;
    decide.approve.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    const { user } = openCard([refundRow()], decide);
    const approve = screen.getByRole("button", { name: "Approve" });
    await user.click(approve);
    await user.click(approve);
    expect(decide.approve).toHaveBeenCalledTimes(1);
    finish(undefined);
    await waitFor(() => expect(approve.hasAttribute("disabled")).toBe(false));
  });

  it("shows the server's words when approving fails, and stops at the change that failed", async () => {
    const decide = decisions();
    decide.approve.mockRejectedValue(
      new ApiError(409, "APPROVAL_EXPIRED", "This approval ran out."),
    );
    const { user } = openCard(BOTH, decide);
    await user.click(screen.getByRole("button", { name: "Approve" }));
    expect((await screen.findByRole("alert")).textContent).toBe("This approval ran out.");
    expect(decide.approve).toHaveBeenCalledTimes(1);
  });

  it("shows plain words when approving fails for a reason it cannot name", async () => {
    const decide = decisions();
    decide.approve.mockRejectedValue(new TypeError("Failed to fetch"));
    const { user } = openCard(BOTH, decide);
    await user.click(screen.getByRole("button", { name: "Approve" }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "That did not go through. Try again.",
    );
  });

  it("clears the error when the next try goes through", async () => {
    const decide = decisions();
    decide.approve.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const { user } = openCard([refundRow()], decide);
    await user.click(screen.getByRole("button", { name: "Approve" }));
    await screen.findByRole("alert");
    await user.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });
});

group("rejecting", () => {
  it("asks for a reason first, and sends nothing until one is given", async () => {
    const { decide, user } = openCard();
    await user.click(screen.getByRole("button", { name: "Reject" }));
    const reason = screen.getByRole("textbox", { name: "Reason for rejecting" });
    expect(document.activeElement).toBe(reason);
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    await user.keyboard("{Enter}");
    await user.type(reason, "   {Enter}");
    expect(decide.reject).not.toHaveBeenCalled();
  });

  it("rejects each open change with the reason, trimmed", async () => {
    const { decide, user } = openCard();
    await user.click(screen.getByRole("button", { name: "Reject" }));
    await user.type(screen.getByRole("textbox"), "  Wrong order.  ");
    await user.click(screen.getByRole("button", { name: "Reject" }));
    expect(decide.reject.mock.calls.map(([action, why]) => [action.actionId, why])).toEqual(
      BOTH.map((row) => [row.actionId, "Wrong order."]),
    );
    expect(decide.approve).not.toHaveBeenCalled();
  });

  it("goes back to the choice without deciding anything", async () => {
    const { decide, user } = openCard();
    await user.click(screen.getByRole("button", { name: "Reject" }));
    await user.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.getByRole("button", { name: "Approve" })).toBeTruthy();
    expect(decide.reject).not.toHaveBeenCalled();
  });

  it("shows the error when rejecting fails", async () => {
    const decide = decisions();
    decide.reject.mockRejectedValue(new ApiError(503, "BACKEND_UNAVAILABLE", "Not answering."));
    const { user } = openCard([refundRow()], decide);
    await user.click(screen.getByRole("button", { name: "Reject" }));
    await user.type(screen.getByRole("textbox"), "Wrong order.{Enter}");
    expect((await screen.findByRole("alert")).textContent).toBe("Not answering.");
  });
});

group("a closed card", () => {
  const SETTLED = [
    noteRow({ status: "done" }),
    refundRow({ status: "rejected" }),
    refundRow({ actionId: "w", status: "waiting", approvalKind: "supervisor" }),
    refundRow({ actionId: "x", status: "expired" }),
    refundRow({ actionId: "y", status: "failed" }),
    refundRow({ actionId: "z", status: "unknown" }),
  ];

  it("lists what was proposed with how each change ended, and offers no decision", () => {
    const { subject } = renderAs(
      <ApprovalCard actions={SETTLED} open={false} decide={decisions()} />,
    );
    expect(screen.getByRole("heading", { name: "Proposed changes" })).toBeTruthy();
    for (const word of ["Done", "Rejected", "Waiting for supervisor", "Not approved in time"]) {
      expect(within(subject).getByText(word)).toBeTruthy();
    }
    expect(within(subject).getByText("Not done")).toBeTruthy();
    expect(within(subject).getByText("Not confirmed")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
    expect(subject.textContent).not.toContain("Nothing has been changed yet.");
  });

  it("shows no state word for a change that was approved and is being made", () => {
    const { subject } = renderAs(
      <ApprovalCard
        actions={[refundRow({ status: "approved" })]}
        open={false}
        decide={decisions()}
      />,
    );
    expect(subject.querySelector("li")?.textContent).not.toMatch(/Done|Rejected|Waiting/);
  });

  it("draws an empty card when nothing was proposed", () => {
    const { subject } = renderAs(<ApprovalCard actions={[]} open={false} decide={decisions()} />);
    expect(subject.querySelectorAll("li")).toHaveLength(0);
  });
});

group("what an engineer is never shown", () => {
  it("has no internal names on an open or a closed card, even with the switch clicked", async () => {
    const { subject, user } = openCard();
    await switchTechnicalOn(user);
    expect(internalWords(subject)).toEqual([]);
    expect(subject.textContent).not.toMatch(/issue_refund|update_ticket|args hash/);
    const closed = renderAs(<ApprovalCard actions={BOTH} open={false} decide={decisions()} />);
    expect(internalWords(closed.subject)).toEqual([]);
  });

  it("adds the technical line only where the demonstration's switch is on", async () => {
    const decide = decisions();
    const { subject, user } = renderAs(
      <ApprovalCard actions={[refundRow()]} open decide={decide} />,
      withDemoTools(ARJUN),
    );
    expect(subject.textContent).not.toContain("issue_refund");
    await switchTechnicalOn(user);
    expect(
      screen.getByText(`issue_refund · args hash ${ARGS_HASH.slice(0, 12)} · ${ACTION_ID}`),
    ).toBeTruthy();
  });
});

group("accessibility", () => {
  it("breaks no rule open, while a reason is asked for, or closed", async () => {
    const { subject, user } = openCard();
    expect(await violations(subject)).toEqual([]);
    await user.click(screen.getByRole("button", { name: "Reject" }));
    expect(await violations(subject)).toEqual([]);
    const closed = renderAs(<ApprovalCard actions={BOTH} open={false} decide={decisions()} />);
    expect(await violations(closed.subject)).toEqual([]);
  });
});

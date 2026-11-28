// @vitest-environment jsdom
import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { RejectForm } from "@/components/thread/reject-form";
import { internalWords, violations } from "@/test/axe";
import { LIMITS } from "@/test/fixtures";
import { renderAs } from "@/test/render";

function form() {
  const onReject = vi.fn<(reason: string) => void>();
  const onBack = vi.fn<() => void>();
  const rendered = renderAs(<RejectForm onReject={onReject} onBack={onBack} />);
  const reason = screen.getByRole("textbox", { name: "Reason for rejecting" });
  const reject = screen.getByRole("button", { name: "Reject" });
  return { ...rendered, onReject, onBack, reason, reject };
}

describe("the reason for a rejection", () => {
  it("is where the cursor is when the form appears", () => {
    const { reason } = form();
    expect(document.activeElement).toBe(reason);
  });

  it("must be given: Reject is disabled while it is empty or only spaces", async () => {
    const { user, reason, reject, onReject } = form();
    expect(reject.hasAttribute("disabled")).toBe(true);
    await user.type(reason, "   ");
    expect(reject.hasAttribute("disabled")).toBe(true);
    await user.keyboard("{Enter}");
    expect(onReject).not.toHaveBeenCalled();
  });

  it("is sent trimmed when Reject is clicked", async () => {
    const { user, reason, reject, onReject } = form();
    await user.type(reason, "  Not the right order. ");
    await user.click(reject);
    expect(onReject).toHaveBeenCalledTimes(1);
    expect(onReject).toHaveBeenCalledWith("Not the right order.");
  });

  it("is sent when Enter is pressed in the field", async () => {
    const { user, reason, onReject } = form();
    await user.type(reason, "x{Enter}");
    expect(onReject).toHaveBeenCalledWith("x");
  });

  it("can be no longer than the limit for a reason", () => {
    const { reason } = form();
    expect(reason.getAttribute("maxlength")).toBe(String(LIMITS.reasonChars));
  });

  it("is not sent by Back, which only goes back", async () => {
    const { user, reason, onReject, onBack } = form();
    await user.type(reason, "Changed my mind");
    await user.click(screen.getByRole("button", { name: "Back" }));
    expect(onBack).toHaveBeenCalledTimes(1);
    expect(onReject).not.toHaveBeenCalled();
  });
});

describe("the form", () => {
  it("breaks no accessibility rule and uses no internal words", async () => {
    const { subject } = form();
    expect(await violations(subject)).toEqual([]);
    expect(internalWords(subject)).toEqual([]);
  });
});

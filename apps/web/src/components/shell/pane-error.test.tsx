// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PaneError } from "@/components/shell/pane-error";
import { ApiError } from "@/lib/api";
import { internalWords, violations } from "@/test/axe";

const failed = (status: number, code: string, message = "From the server.") =>
  new ApiError(status, code, message);

const tryAgain = () => screen.queryByRole("button", { name: "Try again" });

describe("what a failed pane says", () => {
  it.each([
    [403, "PERMISSION_DENIED", "Your role does not include this. Ask a supervisor if you need it."],
    [404, "NOT_FOUND", "Nothing here is visible to you."],
    [429, "RATE_LIMITED", "Too many requests just now. Wait a moment and try again."],
    [503, "BACKEND_UNAVAILABLE", "Meridian's systems are not answering right now."],
  ])("for %d %s: %s", (status, code, words) => {
    render(<PaneError error={failed(status, code)} />);
    expect(screen.getByRole("alert").textContent).toBe(words);
  });

  it("is the server's own message for a code it has no words for", () => {
    render(<PaneError error={failed(409, "CONFLICT", "The ticket changed. Reload it.")} />);
    expect(screen.getByRole("alert").textContent).toBe("The ticket changed. Reload it.");
  });

  it("is the server's own message for a code named like something every object has", () => {
    render(<PaneError error={failed(500, "constructor", "It broke.")} />);
    expect(screen.getByRole("alert").textContent).toBe("It broke.");
  });

  it.each([
    ["a failure of the network", new TypeError("Failed to fetch at http://10.0.0.4:8001")],
    ["something that is not an error", "boom"],
    ["nothing", undefined],
  ])("is only that something went wrong for %s, with none of its detail", (_what, error) => {
    render(<PaneError error={error} />);
    expect(screen.getByRole("alert").textContent).toBe("Something went wrong.");
  });
});

describe("trying again", () => {
  it.each([
    [429, "RATE_LIMITED"],
    [503, "BACKEND_UNAVAILABLE"],
    [500, "UNKNOWN"],
    [200, "UNREADABLE"],
  ])("is offered for %d %s", (status, code) => {
    render(<PaneError error={failed(status, code)} retry={vi.fn()} />);
    expect(tryAgain()).not.toBeNull();
  });

  it("is offered when the failure is not from the server at all", () => {
    render(<PaneError error={new TypeError("Failed to fetch")} retry={vi.fn()} />);
    expect(tryAgain()).not.toBeNull();
  });

  it.each([
    [403, "PERMISSION_DENIED"],
    [404, "NOT_FOUND"],
  ])("is not offered for %d %s, which would only be refused again", (status, code) => {
    render(<PaneError error={failed(status, code)} retry={vi.fn()} />);
    expect(tryAgain()).toBeNull();
  });

  it("is not offered when there is no way to", () => {
    render(<PaneError error={failed(503, "BACKEND_UNAVAILABLE")} />);
    expect(tryAgain()).toBeNull();
  });

  it("tries again on a click", async () => {
    const retry = vi.fn();
    render(<PaneError error={failed(503, "BACKEND_UNAVAILABLE")} retry={retry} />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Try again" }));
    expect(retry).toHaveBeenCalledTimes(1);
  });
});

describe("the pane", () => {
  it("breaks no accessibility rule and uses no internal words", async () => {
    const { container } = render(<PaneError error={failed(429, "RATE_LIMITED")} retry={vi.fn()} />);
    expect(await violations(container)).toEqual([]);
    expect(internalWords(container)).toEqual([]);
  });
});

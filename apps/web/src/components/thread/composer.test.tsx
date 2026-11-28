// @vitest-environment jsdom
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Composer, ConnectionLine } from "@/components/thread/composer";
import { ApiError } from "@/lib/api";
import { internalWords, violations } from "@/test/axe";
import { LIMITS } from "@/test/fixtures";
import { renderAs } from "@/test/render";

const HINT =
  "Copilot reads orders, tickets, runbooks and policies with your permissions. It asks before it changes anything.";

function composer(running = false) {
  const onSend = vi.fn<(message: string) => Promise<void>>().mockResolvedValue(undefined);
  const onStop = vi.fn<() => void>();
  const rendered = renderAs(
    <Composer
      placeholder="Ask about this ticket"
      running={running}
      onSend={onSend}
      onStop={onStop}
    />,
  );
  const box = within(rendered.subject).getByRole<HTMLTextAreaElement>("textbox", {
    name: "Message",
  });
  return { ...rendered, onSend, onStop, box };
}

describe("sending", () => {
  it("sends the message on Enter and empties the box", async () => {
    const { user, onSend, box } = composer();
    await user.type(box, "Why is order 4471 late?{Enter}");
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend).toHaveBeenCalledWith("Why is order 4471 late?");
    await waitFor(() => expect(box.value).toBe(""));
  });

  it("sends the message when the Send button is clicked", async () => {
    const { user, onSend, box } = composer();
    await user.type(box, "Why is order 4471 late?");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(onSend).toHaveBeenCalledWith("Why is order 4471 late?");
  });

  it("makes a new line on Shift and Enter, and sends nothing", async () => {
    const { user, onSend, box } = composer();
    await user.type(box, "First line{Shift>}{Enter}{/Shift}second line");
    expect(onSend).not.toHaveBeenCalled();
    expect(box.value).toBe("First line\nsecond line");
  });

  it.each([
    ["an empty message", ""],
    ["a message of only spaces and new lines", "  {Shift>}{Enter}{/Shift}  "],
  ])("refuses %s and says to type one first", async (_what, typed) => {
    const { user, onSend, box } = composer();
    await user.type(box, `${typed}{Enter}`);
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toBe("Type a message first.");
  });

  it("refuses an empty message from the Send button too", async () => {
    const { user, onSend } = composer();
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toBeTruthy();
  });

  it("shows the hint again once a message is sent after a refusal", async () => {
    const { user, box } = composer();
    await user.type(box, "{Enter}");
    await user.type(box, "Hello{Enter}");
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(screen.getByText(HINT)).toBeTruthy();
  });
});

describe("sending that goes wrong", () => {
  it("keeps the message and says what went wrong when it could not be sent", async () => {
    const { user, onSend, box } = composer();
    onSend.mockRejectedValue(new ApiError(429, "RATE_LIMITED", "Too many requests just now."));
    await user.type(box, "Why is order 4471 late?{Enter}");
    expect((await screen.findByRole("alert")).textContent).toBe("Too many requests just now.");
    expect(box.value).toBe("Why is order 4471 late?");
  });

  it("says it did not send when the failure has no words of its own", async () => {
    const { user, onSend, box } = composer();
    onSend.mockRejectedValue("offline");
    await user.type(box, "Hello{Enter}");
    expect((await screen.findByRole("alert")).textContent).toBe("That did not send. Try again.");
  });

  it("does not send while a word is still being composed on the keyboard", () => {
    const { onSend, box } = composer();
    box.focus();
    box.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Enter", bubbles: true, isComposing: true }),
    );
    expect(onSend).not.toHaveBeenCalled();
  });

  it("allows no more characters than the limit for a message", () => {
    const { box } = composer();
    expect(box.getAttribute("maxlength")).toBe(String(LIMITS.messageChars));
    expect(box.getAttribute("placeholder")).toBe("Ask about this ticket");
  });

  it("sends a message once when Enter is pressed twice before the first send answers", async () => {
    const { user, onSend, box } = composer();
    onSend.mockReturnValue(new Promise(() => undefined));
    await user.type(box, "Why is order 4471 late?{Enter}{Enter}");
    expect(onSend).toHaveBeenCalledTimes(1);
  });
});

describe("while the assistant is working", () => {
  it("offers Stop in place of Send, and stops on a click", async () => {
    const { user, onSend, onStop } = composer(true);
    expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Stop" }));
    expect(onStop).toHaveBeenCalledTimes(1);
    expect(onSend).not.toHaveBeenCalled();
  });

  it("neither sends nor stops on Enter, and keeps what was typed", async () => {
    const { user, onSend, onStop, box } = composer(true);
    await user.type(box, "One more thing{Enter}");
    expect(onSend).not.toHaveBeenCalled();
    expect(onStop).not.toHaveBeenCalled();
    expect(box.value).toBe("One more thing");
  });
});

describe("the composer", () => {
  it("says what the assistant does with the engineer's permissions", () => {
    composer();
    expect(screen.getByText(HINT)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows a connection line as a status", () => {
    renderAs(<ConnectionLine text="Connection lost. Reconnecting." />);
    expect(screen.getByRole("status").textContent).toBe("Connection lost. Reconnecting.");
  });

  it("breaks no accessibility rule and uses no internal words, idle or working", async () => {
    const idle = composer();
    expect(await violations(idle.subject)).toEqual([]);
    expect(internalWords(idle.subject)).toEqual([]);
    const working = composer(true);
    expect(await violations(working.subject)).toEqual([]);
  });
});

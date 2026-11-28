// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatusWord, type Tone } from "@/components/inbox/status-word";
import { violations } from "@/test/axe";

const TONES: readonly Tone[] = ["neutral", "ok", "warn", "bad", "accent"];

describe("a state word", () => {
  it("says the state in words, with a dot that is hidden from screen readers", () => {
    const { container } = render(<StatusWord tone="ok">Refund paid</StatusWord>);
    expect(screen.getByText("Refund paid")).toBeTruthy();
    expect(container.querySelectorAll("[aria-hidden='true']")).toHaveLength(1);
  });

  it("is neutral when no tone is given", () => {
    render(<StatusWord>Escalated by Priya</StatusWord>);
    expect(screen.getByText("Escalated by Priya").className).toContain("text-ink-2");
  });

  it.each([
    ["ok", "text-ok", "bg-ok"],
    ["warn", "text-warn", "bg-warn"],
    ["bad", "text-bad", "bg-bad"],
    ["accent", "text-accent", "bg-accent"],
  ] as const)("colours the word and the dot alike for %s", (tone, text, dot) => {
    const { container } = render(<StatusWord tone={tone}>State</StatusWord>);
    expect(screen.getByText("State").className).toContain(text);
    expect(container.querySelector("[aria-hidden]")?.className).toContain(dot);
  });

  it("breaks no accessibility rule in any tone", async () => {
    const { container } = render(
      <p>
        {TONES.map((tone) => (
          <StatusWord key={tone} tone={tone}>
            {tone}
          </StatusWord>
        ))}
      </p>,
    );
    expect(await violations(container)).toEqual([]);
  });
});

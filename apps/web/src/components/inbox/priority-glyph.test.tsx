// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PriorityGlyph } from "@/components/inbox/priority-glyph";
import type { Priority } from "@/lib/schemas";
import { violations } from "@/test/axe";

function bars(priority: Priority): string[] {
  const { container } = render(<PriorityGlyph priority={priority} />);
  return [...container.querySelectorAll("rect")].map((bar) => bar.getAttribute("class") ?? "");
}

describe("the priority glyph", () => {
  it.each([
    ["P1", 4],
    ["P2", 3],
    ["P3", 2],
    ["P4", 1],
  ] as const)("fills %s as %d of four bars", (priority, filled) => {
    const drawn = bars(priority);
    expect(drawn).toHaveLength(4);
    expect(drawn.filter((bar) => bar !== "fill-line-2")).toHaveLength(filled);
    expect(drawn.slice(filled).every((bar) => bar === "fill-line-2")).toBe(true);
  });

  it("uses the problem colour for P1 only", () => {
    expect(bars("P1").every((bar) => bar === "fill-bad")).toBe(true);
    expect(bars("P2").some((bar) => bar === "fill-bad")).toBe(false);
  });

  it("says the priority in words, since the bars alone say nothing to a screen reader", async () => {
    const { container } = render(<PriorityGlyph priority="P3" />);
    expect(screen.getByRole("img", { name: "Priority P3" })).toBeTruthy();
    expect(await violations(container)).toEqual([]);
  });
});

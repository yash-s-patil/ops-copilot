// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { OrderStageBar } from "@/components/details/order-stage-bar";
import { violations } from "@/test/axe";

function bar(status: string, stuck = false) {
  const { container } = render(<OrderStageBar status={status} stuck={stuck} />);
  const stages = within(container).queryAllByRole("listitem");
  const current = stages.filter((stage) => stage.getAttribute("aria-current") === "step");
  return { container, stages, labels: stages.map((stage) => stage.textContent), current };
}

describe("the stages of an order", () => {
  it("are five, in order", () => {
    expect(bar("placed").labels).toEqual(["Placed", "Paid", "Packed", "Shipped", "Delivered"]);
  });

  it.each([
    ["placed", "Placed"],
    ["paid", "Paid"],
    ["packed", "Packed"],
    ["shipped", "Shipped"],
    ["delivered", "Delivered"],
  ])("mark %s as the one the order is at", (status, label) => {
    const { current } = bar(status);
    expect(current.map((stage) => stage.textContent)).toEqual([label]);
  });

  it("mark the current stage in the warning colour when the order is stuck there", () => {
    expect(bar("shipped", true).current[0]?.className).toContain("border-t-warn");
    expect(bar("shipped", false).current[0]?.className).not.toContain("border-t-warn");
  });

  it("say Lost, in the problem colour, where a lost order was last seen", () => {
    const { labels, current } = bar("lost", true);
    expect(labels).toEqual(["Placed", "Paid", "Packed", "Lost", "Delivered"]);
    expect(current[0]?.textContent).toBe("Lost");
    expect(current[0]?.className).toContain("text-bad");
  });

  it("give way to a sentence for a status that is not a stage", () => {
    const { stages } = bar("cancelled");
    expect(stages).toEqual([]);
    expect(screen.getByText("This order was cancelled.")).toBeTruthy();
  });

  it("break no accessibility rule, as stages or as a sentence", async () => {
    expect(await violations(bar("lost").container)).toEqual([]);
    expect(await violations(bar("returned").container)).toEqual([]);
  });
});

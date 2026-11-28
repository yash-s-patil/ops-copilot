// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Button } from "@/components/ui/button";
import { violations } from "@/test/axe";

describe("Button", () => {
  it("is a plain button that does not submit a form unless asked to", () => {
    render(<Button>Resolve</Button>);
    expect(screen.getByRole("button", { name: "Resolve" }).getAttribute("type")).toBe("button");
  });

  it("submits when it is given the submit type", () => {
    render(<Button type="submit">Reject</Button>);
    expect(screen.getByRole("button").getAttribute("type")).toBe("submit");
  });

  it("calls its handler on a click, and not while disabled", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    const { rerender } = render(<Button onClick={onClick}>Approve</Button>);
    await user.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledTimes(1);
    rerender(
      <Button onClick={onClick} disabled>
        Approve
      </Button>,
    );
    await user.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});

describe("how a Button is drawn", () => {
  it.each([
    ["default", "bg-surface"],
    ["primary", "bg-accent"],
    ["ghost", "border-transparent"],
  ] as const)("is drawn as %s", (variant, style) => {
    render(<Button variant={variant}>Go</Button>);
    expect(screen.getByRole("button").className).toContain(style);
  });

  it("is square for an icon, and keeps a class the caller adds", () => {
    render(
      <Button square className="lg:hidden" aria-label="Close details">
        ×
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Close details" });
    expect(button.className).toContain("w-[30px]");
    expect(button.className).toContain("lg:hidden");
    expect(button.className).not.toContain("px-[11px]");
  });

  it("is large enough to touch on a coarse pointer", () => {
    render(<Button>Go</Button>);
    expect(screen.getByRole("button").className).toContain("pointer-coarse:min-h-11");
  });

  it("breaks no accessibility rule", async () => {
    const { container } = render(
      <div>
        <Button>Resolve</Button>
        <Button variant="primary" disabled>
          Approve
        </Button>
        <Button square aria-label="Ticket details">
          <svg aria-hidden="true" />
        </Button>
      </div>,
    );
    expect(await violations(container)).toEqual([]);
  });
});

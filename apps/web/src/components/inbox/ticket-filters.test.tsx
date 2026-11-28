// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TicketFilters } from "@/components/inbox/ticket-filters";
import { violations } from "@/test/axe";

const COUNTS = { my_queue: 3, all_open: 14, waiting: 0, done: 120 };

const links = () => screen.getAllByRole("link");

describe("the four views of the inbox", () => {
  it("are listed in order, each with its count", () => {
    render(<TicketFilters view="my_queue" counts={COUNTS} ticketId={null} />);
    expect(screen.getByRole("navigation", { name: "Filter" })).toBeTruthy();
    expect(links().map((link) => link.textContent)).toEqual([
      "My queue3",
      "All open14",
      "Waiting0",
      "Done120",
    ]);
  });

  it("have no counts until the tickets have been listed", () => {
    render(<TicketFilters view="my_queue" counts={null} ticketId={null} />);
    expect(links().map((link) => link.textContent)).toEqual([
      "My queue",
      "All open",
      "Waiting",
      "Done",
    ]);
  });

  it.each(["my_queue", "all_open", "waiting", "done"] as const)(
    "mark %s, and only it, as the current one",
    (view) => {
      render(<TicketFilters view={view} counts={COUNTS} ticketId={null} />);
      const current = links().filter((link) => link.getAttribute("aria-current") === "true");
      expect(current.map((link) => link.getAttribute("href"))).toEqual([`/inbox?view=${view}`]);
    },
  );

  it("lead to the list when no ticket is open", () => {
    render(<TicketFilters view="done" counts={COUNTS} ticketId={null} />);
    expect(links().map((link) => link.getAttribute("href"))).toEqual([
      "/inbox?view=my_queue",
      "/inbox?view=all_open",
      "/inbox?view=waiting",
      "/inbox?view=done",
    ]);
  });

  it("keep the open ticket open when the view changes", () => {
    render(<TicketFilters view="done" counts={COUNTS} ticketId={2207} />);
    expect(links()[2]?.getAttribute("href")).toBe("/inbox/2207?view=waiting");
  });

  it("break no accessibility rule", async () => {
    const { container } = render(<TicketFilters view="waiting" counts={COUNTS} ticketId={2207} />);
    expect(await violations(container)).toEqual([]);
  });
});

/**
 * Every route, in both themes: no accessibility violation that axe can find, and a page
 * that never scrolls sideways. The narrowest project also checks 360 px, the least width
 * the product supports (SPEC 8.4).
 */
import type { Page } from "@playwright/test";
import {
  expectNoSideScroll,
  hasDrawer,
  investigate,
  isPhone,
  openDetails,
  openTicket,
  send,
  setTheme,
  signIn,
  violationsOn,
} from "./support/app";
import type { FakeAgent } from "./support/fake";
import type { Scene } from "./support/fake";
import { TICKETS, type User } from "./support/story";
import { expect, test } from "./support/test";

const THEMES = ["light", "dark"] as const;
const NARROWEST = { width: 360, height: 740 };

type Screen = {
  name: string;
  user: User | null;
  scenes?: Scene[];
  /** Brings the page to the state under test. Run again after the theme changes. */
  arrive: (page: Page, fake: FakeAgent) => Promise<void>;
};

const SCREENS: Screen[] = [
  {
    name: "/sign-in",
    user: null,
    arrive: async (page) => {
      await page.goto("/sign-in");
      await expect(page.getByRole("button", { name: /Priya Nair/ })).toBeVisible();
    },
  },
  {
    name: "/inbox",
    user: "priya",
    arrive: async (page) => {
      await page.goto("/inbox?view=all_open");
      const visible = isPhone(page)
        ? page.getByRole("region", { name: "Tickets" })
        : page.getByRole("region", { name: "Ticket", exact: true });
      await expect(visible).toBeVisible();
    },
  },
  {
    name: "/inbox empty",
    user: "rohan",
    arrive: async (page) => {
      await page.goto("/inbox?view=my_queue");
      await expect(page.getByText("Your queue is clear.")).toBeVisible();
    },
  },
  {
    name: "/inbox/[ticketId] with an approval card",
    user: "priya",
    arrive: async (page) => {
      await openTicket(page, TICKETS.stuck);
      const card = page.getByRole("group", { name: "Approve these changes" });
      const start = page.getByRole("button", { name: "Investigate with Copilot" });
      await expect(card.or(start)).toBeVisible();
      if (await start.isVisible()) await investigate(page);
      await expect(card).toBeVisible();
    },
  },
  {
    name: "/inbox/[ticketId] escalated, with details",
    user: "arjun",
    scenes: ["escalated"],
    arrive: async (page) => {
      await openTicket(page, TICKETS.stuck, "my_queue");
      await expect(page.getByText("Escalated to L2 by Priya Nair")).toBeVisible();
      if (hasDrawer(page)) await openDetails(page);
    },
  },
  {
    name: "/inbox/[ticketId] with a refund waiting for the supervisor",
    user: "kavita",
    scenes: ["waiting_for_supervisor"],
    arrive: async (page) => {
      await openTicket(page, TICKETS.lost, "my_queue");
      await expect(page.getByRole("button", { name: "Approve refund" })).toBeVisible();
    },
  },
  {
    name: "/inbox/[ticketId] resolved",
    user: "priya",
    arrive: async (page) => {
      await openTicket(page, TICKETS.resolved, "done");
      await expect(page.getByText("Ticket resolved")).toBeVisible();
    },
  },
  {
    name: "/ask",
    user: "priya",
    arrive: async (page) => {
      await page.goto("/ask");
      await expect(page.getByText("Not tied to a ticket", { exact: true })).toBeVisible();
    },
  },
  {
    name: "/ask/[runId]",
    user: "priya",
    arrive: async (page) => {
      await page.goto("/ask");
      await send(page, "What is wrong with order 4471?");
      await expect(page.getByRole("heading", { name: "Summary" })).toBeVisible();
    },
  },
  {
    name: "/admin/live",
    user: "sam",
    arrive: async (page, fake) => {
      await fake.investigationWaits();
      await page.goto("/admin/live");
      await expect(page.getByText("approval required").first()).toBeVisible();
    },
  },
  {
    name: "/admin/runs",
    user: "sam",
    arrive: async (page, fake) => {
      await fake.investigationWaits();
      await page.goto("/admin/runs");
      await expect(page.getByRole("row").filter({ hasText: "Priya Nair" }).first()).toBeVisible();
    },
  },
  {
    name: "/admin/runs/[runId]",
    user: "sam",
    arrive: async (page, fake) => {
      await fake.investigationWaits();
      await page.goto("/admin/runs");
      await page
        .getByRole("row")
        .filter({ hasText: "Priya Nair" })
        .first()
        .getByRole("link")
        .click();
      await expect(page.getByText("approval required").first()).toBeVisible();
    },
  },
];

for (const screen of SCREENS) {
  test.describe(`${screen.name}`, () => {
    test.beforeEach(async ({ page, fake }) => {
      await fake.reset(...(screen.scenes ?? []));
      if (screen.user !== null) await signIn(page, screen.user);
    });

    for (const theme of THEMES) {
      test(`does not scroll sideways, ${theme}`, async ({ page, fake }) => {
        await screen.arrive(page, fake);
        await setTheme(page, theme);
        await screen.arrive(page, fake);
        await expectNoSideScroll(page);
      });

      test(`has no accessibility violation, ${theme}`, async ({ page, fake }) => {
        await screen.arrive(page, fake);
        await setTheme(page, theme);
        await screen.arrive(page, fake);
        expect(await violationsOn(page)).toEqual([]);
      });
    }

    test("does not scroll sideways at 360 px", async ({ page, fake }) => {
      test.skip(!isPhone(page), "Checked once, in the narrowest project.");
      await page.setViewportSize(NARROWEST);
      await screen.arrive(page, fake);
      await expectNoSideScroll(page);
    });
  });
}

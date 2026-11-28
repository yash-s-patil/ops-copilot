import {
  detailsPane,
  hasDrawer,
  isPhone,
  openTicket,
  signIn,
  ticketList,
  ticketPane,
} from "./support/app";
import { TICKETS } from "./support/story";
import { expect, test } from "./support/test";

test.describe("the theme", () => {
  test("a chosen theme is applied at once and kept across visits", async ({ page }) => {
    await signIn(page, "priya");
    const html = page.locator("html");
    const menu = page.getByRole("button", { name: /^Priya Nair.*theme and sign out$/ });
    await expect(html).not.toHaveAttribute("data-theme");
    const light = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);

    await menu.click();
    await page.getByRole("menuitemradio", { name: "Dark" }).click();
    await expect(html).toHaveAttribute("data-theme", "dark");
    expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).not.toBe(
      light,
    );
    await page.reload();
    await expect(html).toHaveAttribute("data-theme", "dark");
    await menu.click();
    await expect(page.getByRole("menuitemradio", { name: "Dark" })).toBeChecked();
    await page.keyboard.press("Escape");
    await page.goto("/ask");
    await expect(html).toHaveAttribute("data-theme", "dark");

    await menu.click();
    await page.getByRole("menuitemradio", { name: "Light" }).click();
    await expect(html).toHaveAttribute("data-theme", "light");
    await menu.click();
    await page.getByRole("menuitemradio", { name: "Match the system" }).click();
    await expect(html).not.toHaveAttribute("data-theme");
    await page.reload();
    await expect(html).not.toHaveAttribute("data-theme");
  });
});

test.describe("the details pane", () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page, "priya");
    await openTicket(page, TICKETS.stuck);
  });

  test("is a drawer below 1280 px: a button opens it, a button or Escape closes it", async ({
    page,
  }) => {
    test.skip(!hasDrawer(page), "At 1280 px and up the details are a pane of their own.");
    const pane = detailsPane(page);
    const open = page.getByRole("button", { name: "Ticket details" });
    await expect(pane).toBeHidden();
    await open.click();
    await expect(pane).toBeVisible();
    await expect(pane.getByRole("heading", { name: "Details" })).toBeVisible();
    await expect(pane.getByText("Ananya Kulkarni", { exact: true })).toBeVisible();
    await expect(pane.getByRole("heading", { name: "Order 4471" })).toBeVisible();
    await pane.getByRole("button", { name: "Close details" }).click();
    await expect(pane).toBeHidden();

    await open.click();
    await expect(pane).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(pane).toBeHidden();
  });

  test("is always beside the ticket at 1280 px and up", async ({ page }) => {
    test.skip(hasDrawer(page), "Below 1280 px the details are a drawer.");
    const pane = detailsPane(page);
    await expect(pane).toBeVisible();
    await expect(page.getByRole("button", { name: "Ticket details" })).toBeHidden();
    await expect(pane.getByRole("button", { name: "Close details" })).toBeHidden();
    for (const section of ["Customer", "Order 4471", "Refund", "Ticket activity"]) {
      await expect(pane.getByRole("heading", { name: section, exact: true })).toBeVisible();
    }
    await expect(pane.getByText("Packed for 96 hours")).toBeVisible();
    await expect(pane.getByText(/INR\s6,200\.00/)).toBeVisible();
  });
});

test.describe("one pane or several", () => {
  test("a phone shows the list or the ticket, with a way back", async ({ page }) => {
    test.skip(!isPhone(page), "Wider screens show the list beside the ticket.");
    await signIn(page, "priya");
    await expect(page).toHaveURL(/\/inbox$/);
    await expect(ticketList(page)).toBeVisible();
    await expect(page.getByText("Select a ticket from the list.")).toBeHidden();

    await ticketList(page)
      .getByRole("link", { name: /Order stuck for four days/ })
      .click();
    await expect(ticketPane(page).getByRole("heading", { level: 1 })).toBeVisible();
    await expect(ticketList(page)).toBeHidden();

    await page.getByRole("link", { name: "Back to list" }).click();
    await expect(ticketList(page)).toBeVisible();
    await expect(ticketPane(page)).toHaveCount(0);
    // The top bar keeps the way to every screen.
    await page
      .getByRole("navigation", { name: "Main" })
      .getByRole("link", { name: "Ask Copilot" })
      .click();
    await expect(page).toHaveURL(/\/ask$/);
  });

  test("wider screens show the list beside the ticket and no back control", async ({ page }) => {
    test.skip(isPhone(page), "A phone shows one pane at a time.");
    await signIn(page, "priya");
    await openTicket(page, TICKETS.stuck, "my_queue");
    await expect(ticketList(page)).toBeVisible();
    await expect(ticketPane(page)).toBeVisible();
    await expect(page.getByRole("link", { name: "Back to list" })).toBeHidden();
    await ticketList(page)
      .getByRole("link", { name: /Tracking shows/ })
      .click();
    await expect(ticketPane(page).getByRole("heading", { level: 1 })).toHaveText(
      "Tracking shows a carrier exception",
    );
  });

  test("the rail names its screens at every width", async ({ page }) => {
    await signIn(page, "priya");
    const nav = page.getByRole("navigation", { name: "Main" });
    await expect(nav.locator('a[href="/inbox"]')).toHaveAttribute("aria-current", "page");
    await expect(nav.getByRole("link", { name: /Inbox/ })).toBeVisible();
    await expect(nav.getByRole("link", { name: /Ask Copilot/ })).toBeVisible();
  });
});

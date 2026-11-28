import { alerts, isPhone, openTicket, signIn, ticketList, ticketPane } from "./support/app";
import { TICKETS } from "./support/story";
import { expect, test } from "./support/test";

const filters = (page: Parameters<typeof ticketList>[0]) =>
  page.getByRole("navigation", { name: "Filter" });

test("shows each view with its count", async ({ page }) => {
  await signIn(page, "priya");
  const nav = filters(page);
  await expect(nav.getByRole("link", { name: /My queue\s*2$/ })).toHaveAttribute(
    "aria-current",
    "true",
  );
  await expect(nav.getByRole("link", { name: /All open\s*3$/ })).toBeVisible();
  await expect(nav.getByRole("link", { name: /Waiting\s*0$/ })).toBeVisible();
  await expect(nav.getByRole("link", { name: /Done\s*1$/ })).toBeVisible();
});

test("a row says what, which ticket and order, and whose", async ({ page }) => {
  await signIn(page, "priya");
  const row = ticketList(page).getByRole("link", { name: /Order stuck for four days/ });
  await expect(row).toContainText(`#${TICKETS.stuck} · Order 4471 · Ananya Kulkarni · premium`);
  await expect(row.getByRole("img", { name: "Priority P2" })).toBeVisible();
});

test("the filters change what is listed", async ({ page }) => {
  await signIn(page, "priya");
  const list = ticketList(page);
  await filters(page)
    .getByRole("link", { name: /All open/ })
    .click();
  await expect(page).toHaveURL(/view=all_open/);
  await expect(list.getByRole("link", { name: /Parcel lost by carrier/ })).toBeVisible();
  await filters(page).getByRole("link", { name: /Done/ }).click();
  await expect(list.getByRole("link", { name: /Paid but not shipped/ })).toBeVisible();
  await expect(list.getByRole("link", { name: /Order stuck/ })).toBeHidden();
  await filters(page)
    .getByRole("link", { name: /Waiting/ })
    .click();
  await expect(list.getByText("Nothing is waiting on someone else.")).toBeVisible();
});

test("the first ticket of the view opens beside the list", async ({ page }) => {
  test.skip(isPhone(page), "A phone shows the list alone.");
  await signIn(page, "priya");
  await expect(page).toHaveURL(new RegExp(`/inbox/${TICKETS.stuck}\\?view=my_queue`));
  await expect(ticketPane(page).getByRole("heading", { level: 1 })).toHaveText(
    "Order stuck for four days",
  );
  await expect(
    ticketList(page).getByRole("link", { name: /Order stuck for four days/ }),
  ).toHaveAttribute("aria-current", "true");
});

test("an empty view says so in one sentence", async ({ page }) => {
  await signIn(page, "rohan");
  const list = ticketList(page);
  await expect(list.getByText("Your queue is clear.")).toBeVisible();
  if (!isPhone(page)) await expect(page.getByText("Select a ticket from the list.")).toBeVisible();
  await page.goto("/inbox?view=all_open");
  await expect(list.getByText("No open tickets in your region.")).toBeVisible();
  await page.goto("/inbox?view=done");
  await expect(list.getByText("No resolved tickets yet.")).toBeVisible();
});

test("a supervisor with nothing to approve is told so", async ({ page }) => {
  await signIn(page, "kavita");
  await expect(
    ticketList(page).getByText("No refunds are waiting for your approval."),
  ).toBeVisible();
});

test("the arrow keys move between rows and Enter opens one", async ({ page }) => {
  await signIn(page, "priya");
  await page.goto("/inbox?view=all_open");
  const rows = ticketList(page).locator("[data-ticket-row]");
  await expect(rows).toHaveCount(3);
  await rows.nth(0).focus();
  await page.keyboard.press("ArrowDown");
  await expect(rows.nth(1)).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(rows.nth(2)).toBeFocused();
  await page.keyboard.press("ArrowUp");
  await expect(rows.nth(1)).toBeFocused();
  const opened = await rows.nth(1).getAttribute("href");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`${opened?.replace("?", "\\?")}$`));
  await expect(ticketPane(page).getByRole("heading", { level: 1 })).toBeVisible();
});

test("a ticket of another region does not exist for this engineer", async ({ page }) => {
  await signIn(page, "rohan");
  await page.goto(`/inbox/${TICKETS.stuck}`);
  await expect(alerts(page)).toHaveText("Nothing here is visible to you.");
  await expect(page.getByText("Ananya Kulkarni")).toHaveCount(0);
});

test("a ticket opens with the customer's words and its details", async ({ page }) => {
  await signIn(page, "priya");
  await openTicket(page, TICKETS.stuck);
  const pane = ticketPane(page);
  await expect(pane.getByText(`#${TICKETS.stuck} · Order 4471 · P2 · L1 queue`)).toBeVisible();
  await expect(pane.getByText("My order has not moved for days. Can someone check?")).toBeVisible();
  await expect(pane.getByRole("button", { name: "Investigate with Copilot" })).toBeVisible();
});

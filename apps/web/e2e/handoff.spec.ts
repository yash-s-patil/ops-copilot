import {
  closeDetails,
  investigate,
  openDetails,
  openTicket,
  signIn,
  switchTo,
  ticketList,
  ticketPane,
} from "./support/app";
import { SMALL_REFUND, TICKETS } from "./support/story";
import { expect, test } from "./support/test";

test("the escalated ticket leaves L1's queue and waits with L2", async ({ page }) => {
  await signIn(page, "priya");
  await openTicket(page, TICKETS.stuck, "my_queue");
  const card = await investigate(page);
  await card.getByRole("button", { name: "Approve" }).click();
  await expect(ticketPane(page).getByText("Approved by Priya Nair")).toBeVisible();

  await page.goto("/inbox?view=waiting");
  const row = ticketList(page).getByRole("link", { name: /Order stuck for four days/ });
  await expect(row.getByText("Escalated by Priya")).toBeVisible();
  await expect(row.getByText("Refund recommended")).toBeVisible();
  await page.goto("/inbox?view=my_queue");
  await expect(ticketList(page).getByRole("link", { name: /Tracking shows/ })).toBeVisible();
  await expect(ticketList(page).getByRole("link", { name: /Order stuck/ })).toBeHidden();

  await switchTo(page, "arjun");
  await page.goto("/inbox?view=my_queue");
  const mine = ticketList(page).getByRole("link", { name: /Order stuck for four days/ });
  await expect(mine.getByText("Escalated by Priya")).toBeVisible();
  await expect(mine.getByText("Refund recommended")).toBeVisible();
});

test("L2 sees who escalated, the recommendation and the earlier investigation", async ({
  page,
  fake,
}) => {
  await fake.reset("escalated");
  await signIn(page, "arjun");
  await openTicket(page, TICKETS.stuck, "my_queue");
  const pane = ticketPane(page);
  await expect(pane.getByText("Escalated to L2 by Priya Nair")).toBeVisible();
  await expect(
    pane.getByText(/Recommended refund: INR\s620\.00 \(goodwill delay\)\./),
  ).toBeVisible();
  await expect(pane.getByText(/^Investigation by Priya Nair · \d\d:\d\d$/)).toBeVisible();
  await expect(
    pane.getByText(/The order has been packed for 96 hours.*Ticket updated\./),
  ).toBeVisible();
  await expect(pane.getByRole("button", { name: "Investigate with Copilot" })).toBeVisible();
  // Another person's investigation is a summary, never their conversation.
  await expect(pane.getByText("Investigate this ticket and propose a resolution.")).toBeHidden();

  const details = await openDetails(page);
  await expect(details.getByText(/Recommended by Priya Nair:/)).toContainText(SMALL_REFUND);
  await expect(details.getByText("Copilot, for Priya Nair")).toBeVisible();
  await expect(details.getByText("ananya.k@example.in")).toBeVisible();
  await closeDetails(page);
});

test("L1 sees contact details masked, and is told why", async ({ page }) => {
  await signIn(page, "priya");
  await openTicket(page, TICKETS.stuck);
  const details = await openDetails(page);
  await expect(details.getByText("Contact details are masked for L1.")).toBeVisible();
  await expect(details.getByText("ananya.k@example.in")).toHaveCount(0);
  await expect(details.getByText("No refund on this order.")).toBeVisible();
});

import { investigate, openTicket, signIn, ticketList, ticketPane } from "./support/app";
import { TICKETS } from "./support/story";
import { expect, test } from "./support/test";

test("Resolve asks to confirm in place, then the ticket is done", async ({ page, fake }) => {
  await signIn(page, "priya");
  await openTicket(page, TICKETS.exception, "my_queue");
  const pane = ticketPane(page);
  await pane.getByRole("button", { name: "Resolve", exact: true }).click();
  expect(await fake.seen((entry) => entry.path.endsWith("/resolve"))).toEqual([]);
  await pane.getByRole("button", { name: "Confirm resolve" }).click();
  await expect(pane.getByText("Ticket resolved")).toBeVisible();
  await expect(pane.getByText("Resolved", { exact: true })).toBeVisible();
  await expect(pane.getByRole("button", { name: /resolve/i })).toBeHidden();
  await expect(pane.getByRole("button", { name: "Investigate with Copilot" })).toBeHidden();

  const sent = await fake.seen((entry) => entry.path.endsWith("/resolve"));
  expect(sent).toHaveLength(1);
  expect(sent[0]?.idempotency_key).toMatch(/^[0-9a-f]{32}$/);

  await page.goto("/inbox?view=done");
  await expect(ticketList(page).getByRole("link", { name: /Tracking shows/ })).toBeVisible();
  await page.goto("/inbox?view=my_queue");
  await expect(ticketList(page).getByRole("link", { name: /Tracking shows/ })).toBeHidden();
});

test("a resolved ticket is not offered Resolve again", async ({ page }) => {
  await signIn(page, "priya");
  await openTicket(page, TICKETS.resolved, "done");
  const pane = ticketPane(page);
  await expect(pane.getByText("Resolved", { exact: true })).toBeVisible();
  await expect(pane.getByRole("button", { name: "Resolve", exact: true })).toHaveCount(0);
});

test("changing one's mind before confirming changes nothing", async ({ page, fake }) => {
  await signIn(page, "priya");
  await openTicket(page, TICKETS.exception, "my_queue");
  await ticketPane(page).getByRole("button", { name: "Resolve", exact: true }).click();
  await expect(ticketPane(page).getByRole("button", { name: "Confirm resolve" })).toBeVisible();
  await investigate(page);
  expect(await fake.seen((entry) => entry.path.endsWith("/resolve"))).toEqual([]);
  await expect(ticketPane(page).getByText("L1 queue")).toBeVisible();
});

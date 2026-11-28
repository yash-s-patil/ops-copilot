import {
  closeDetails,
  investigate,
  openDetails,
  openTicket,
  signIn,
  ticketPane,
} from "./support/app";
import { SMALL_REFUND, TICKETS } from "./support/story";
import { expect, test } from "./support/test";

const PAYMENT_MS = 20_000;

test.beforeEach(async ({ page, fake }) => {
  await fake.reset("escalated");
  await signIn(page, "arjun");
  await openTicket(page, TICKETS.stuck, "my_queue");
});

test("the card says exactly what will change and that L2 may approve it", async ({ page }) => {
  const card = await investigate(page);
  await expect(card.getByText(/Refund INR\s620\.00 on order 4471/)).toBeVisible();
  await expect(card.getByText(/You can approve refunds up to INR 5,000 yourself\./)).toBeVisible();
  await expect(card.getByText(`Add a note to ticket #${TICKETS.stuck}`)).toBeVisible();
  await expect(ticketPane(page).getByRole("button", { name: "Resolve" })).toBeVisible();
});

test("an approved refund goes to payments and ends as paid", async ({ page }) => {
  const card = await investigate(page);
  await card.getByRole("button", { name: "Approve" }).click();
  const pane = ticketPane(page);
  await expect(pane.getByText("Approved by Arjun Mehta")).toBeVisible();
  await expect(pane.getByRole("heading", { name: SMALL_REFUND })).toBeVisible();
  await expect(
    pane.getByText(/Refund of INR\s620\.00 approved and handed to payments/),
  ).toBeVisible();
  // While payment is under way the ticket cannot be resolved.
  await expect(pane.getByRole("button", { name: "Resolve" })).toBeHidden();
  const progress = pane.getByRole("list").filter({ hasText: "Sent to payments" });
  await expect(progress.getByText("Paid")).toHaveAttribute("aria-current", "step", {
    timeout: PAYMENT_MS,
  });
  const details = await openDetails(page);
  await expect(details.getByText("Paid", { exact: true }).first()).toBeVisible({
    timeout: PAYMENT_MS,
  });
  await expect(details.getByText("Payments", { exact: true })).toBeVisible();
  await closeDetails(page);
  await expect(progress.getByText("Paid")).not.toHaveAttribute("aria-current", "step");
  await expect(pane.getByRole("button", { name: "Resolve" })).toBeVisible();
});

test("the reply to the customer is a draft the engineer copies", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  const card = await investigate(page);
  await card.getByRole("button", { name: "Approve" }).click();
  const pane = ticketPane(page);
  await expect(pane.getByRole("heading", { name: "Reply to the customer" })).toBeVisible();
  await expect(pane.getByText("A draft. You send it yourself.")).toBeVisible();
  await pane.getByRole("button", { name: "Copy draft" }).click();
  await expect(pane.getByRole("button", { name: "Copied" })).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toMatch(/^Hello Ananya,/);
});

test("a failed payment says so and can be sent again", async ({ page, fake }) => {
  await fake.set({ fail_next_refund: true, payment_step_ms: 400 });
  const card = await investigate(page);
  await card.getByRole("button", { name: "Approve" }).click();
  const pane = ticketPane(page);
  await expect(pane.getByText("Payment failed")).toBeVisible({ timeout: PAYMENT_MS });
  const details = await openDetails(page);
  await expect(details.getByText("Payment failed").first()).toBeVisible();
  await details.getByRole("button", { name: "Retry payment" }).click();
  await expect(details.getByText("Paid", { exact: true }).first()).toBeVisible({
    timeout: PAYMENT_MS,
  });
  await expect(details.getByRole("button", { name: "Retry payment" })).toBeHidden();
  await closeDetails(page);
  await expect(pane.getByText("Payment failed")).toBeHidden();
});

test("L1 is not offered Retry payment", async ({ page, fake }) => {
  await fake.set({ fail_next_refund: true, payment_step_ms: 100 });
  const card = await investigate(page);
  await card.getByRole("button", { name: "Approve" }).click();
  await expect(ticketPane(page).getByText("Payment failed")).toBeVisible({ timeout: PAYMENT_MS });
  await page.context().clearCookies({ name: /authjs/ });
  await signIn(page, "priya");
  await openTicket(page, TICKETS.stuck, "waiting");
  const details = await openDetails(page);
  await expect(details.getByText("Payment failed").first()).toBeVisible();
  await expect(details.getByRole("button", { name: "Retry payment" })).toHaveCount(0);
});

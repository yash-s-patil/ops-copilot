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
import { LARGE_REFUND, TICKETS } from "./support/story";
import { expect, test } from "./support/test";

const PAYMENT_MS = 20_000;
const isExecute = (entry: { path: string }) => entry.path.endsWith("/execute");

test("the requester is told a supervisor must approve, and waits", async ({ page }) => {
  await signIn(page, "arjun");
  await openTicket(page, TICKETS.lost, "my_queue");
  const card = await investigate(page);
  await expect(card.getByText(/Refund INR\s8,900\.00 on order 3912/)).toBeVisible();
  await expect(card.getByText(/Above INR 5,000, so a supervisor must approve\./)).toBeVisible();
  await expect(card.getByText("Waiting for supervisor")).toBeVisible();
  await card.getByRole("button", { name: "Approve" }).click();

  const pane = ticketPane(page);
  const proposed = pane.getByRole("heading", { name: "Proposed changes" }).locator("..");
  await expect(proposed.getByText("Waiting for supervisor")).toBeVisible();
  await expect(pane.getByRole("heading", { name: LARGE_REFUND })).toBeHidden();
  // Nobody with only L2's rights is shown the supervisor's banner.
  await expect(pane.getByRole("button", { name: "Approve refund" })).toHaveCount(0);

  await page.goto("/inbox?view=waiting");
  await expect(
    ticketList(page)
      .getByRole("link", { name: /Parcel lost by carrier/ })
      .getByText("Refund waiting for supervisor"),
  ).toBeVisible();
});

test("a supervisor approves, and the requester's next visit carries it out", async ({
  page,
  fake,
}) => {
  await fake.reset("waiting_for_supervisor");
  await signIn(page, "kavita");
  const row = ticketList(page).getByRole("link", { name: /Parcel lost by carrier/ });
  await expect(row.getByText("Needs your approval")).toBeVisible();
  await openTicket(page, TICKETS.lost, "my_queue");
  const pane = ticketPane(page);
  await expect(pane.getByText(/Refund of INR\s8,900\.00 needs your approval/)).toBeVisible();
  await expect(
    pane.getByText(/Requested by Arjun Mehta at \d\d:\d\d\. Policy check:/),
  ).toBeVisible();
  // A supervisor deciding a refund is not offered an investigation of their own beside it.
  await expect(pane.getByRole("button", { name: "Investigate with Copilot" })).toBeHidden();
  await pane.getByRole("button", { name: "Approve refund" }).click();
  await expect(pane.getByText(/You approved a refund of INR\s8,900\.00/)).toBeVisible();
  await expect(pane.getByText(/It completes when Arjun next opens the ticket\./)).toBeVisible();
  await expect(pane.getByRole("button", { name: "Approve refund" })).toBeHidden();
  // Approving is not paying: nothing is carried out until the requester returns.
  expect(await fake.seen(isExecute)).toEqual([]);
  const details = await openDetails(page);
  await expect(details.getByText("approved by supervisor")).toBeVisible();
  await closeDetails(page);

  await switchTo(page, "arjun");
  await openTicket(page, TICKETS.lost, "waiting");
  const mine = ticketPane(page);
  await expect(mine.getByText("Approved by Kavita Rao")).toBeVisible();
  await expect(mine.getByRole("heading", { name: LARGE_REFUND })).toBeVisible();
  await expect(
    mine.getByText(/Refund of INR\s8,900\.00 approved and handed to payments/),
  ).toBeVisible();
  const carriedOut = await fake.seen(isExecute);
  expect(carriedOut.map((entry) => entry.user)).toEqual(["arjun"]);
  const paid = await openDetails(page);
  await expect(paid.getByText("Paid", { exact: true }).first()).toBeVisible({
    timeout: PAYMENT_MS,
  });
});

test("a supervisor rejects with a reason, and the requester sees it", async ({ page, fake }) => {
  await fake.reset("waiting_for_supervisor");
  await signIn(page, "kavita");
  await openTicket(page, TICKETS.lost, "my_queue");
  const pane = ticketPane(page);
  await pane.getByRole("button", { name: "Reject" }).click();
  const reason = pane.getByPlaceholder("Why are you rejecting this?");
  await expect(reason).toBeFocused();
  await expect(pane.getByRole("button", { name: "Reject" })).toBeDisabled();
  await reason.fill("The carrier is still searching. Wait two days.");
  await pane.getByRole("button", { name: "Reject" }).click();
  await expect(pane.getByText(/needs your approval/)).toBeHidden();
  await page.goto("/inbox?view=my_queue");
  await expect(
    ticketList(page).getByText("No refunds are waiting for your approval."),
  ).toBeVisible();

  await switchTo(page, "arjun");
  await openTicket(page, TICKETS.lost, "my_queue");
  const mine = ticketPane(page);
  await expect(mine.getByText("Rejected by Kavita Rao")).toBeVisible();
  await expect(mine.getByRole("heading", { name: LARGE_REFUND })).toBeHidden();
  const details = await openDetails(page);
  await expect(details.getByText("No refund on this order.")).toBeVisible();
  expect(await fake.seen(isExecute)).toEqual([]);
});

test("the details show the refund as waiting as soon as it is asked for", async ({ page }) => {
  await signIn(page, "arjun");
  await openTicket(page, TICKETS.lost, "my_queue");
  await investigate(page);
  const details = await openDetails(page);
  await expect(details.getByText("waiting for supervisor")).toBeVisible();
});

test("after the page is read again the details show the refund as waiting", async ({ page }) => {
  await signIn(page, "arjun");
  await openTicket(page, TICKETS.lost, "my_queue");
  await investigate(page);
  await page.reload();
  const details = await openDetails(page);
  await expect(details.getByText("waiting for supervisor")).toBeVisible();
  await expect(details.getByText(LARGE_REFUND)).toHaveCount(2);
});

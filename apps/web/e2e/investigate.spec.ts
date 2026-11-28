import { approvalCard, investigate, openTicket, send, signIn, ticketPane } from "./support/app";
import { TICKETS } from "./support/story";
import { expect, test } from "./support/test";

const TOOL_NAMES = /get_order|lookup_customer|search_runbooks|update_ticket|issue_refund/;

test.beforeEach(async ({ page }) => {
  await signIn(page, "priya");
  await openTicket(page, TICKETS.stuck);
});

test("the assistant works in plain steps, then asks before it changes anything", async ({
  page,
}) => {
  const card = await investigate(page);
  const pane = ticketPane(page);
  await expect(pane.getByText("Investigate this ticket and propose a resolution.")).toBeVisible();
  await expect(pane.getByText(/I will check order 4471/)).toBeVisible();
  await expect(card).toBeFocused();
  await expect(
    card.getByText(`Add a note and escalate ticket #${TICKETS.stuck} to L2`),
  ).toBeVisible();
  await expect(card.getByText("Nothing has been changed yet.")).toBeVisible();
  await expect(card.getByRole("button", { name: "Approve" })).toBeEnabled();
  await expect(card.getByRole("button", { name: "Reject" })).toBeEnabled();
  await expect(pane.getByRole("button", { name: "Investigate with Copilot" })).toBeHidden();
  await expect(pane).not.toContainText(TOOL_NAMES);
});

test("the steps fold into one line that opens to what each step found", async ({ page }) => {
  await investigate(page);
  const pane = ticketPane(page);
  const group = pane.getByText(/^6 steps · \d+\.\d s$/);
  await expect(group).toBeVisible();
  await expect(pane.getByText("Checked order 4471")).toBeHidden();
  await group.click();
  for (const label of [
    "Checked order 4471",
    "Looked up the customer",
    "Read the ticket notes",
    "Searched the runbooks",
    "Read the refund policy",
    "Prepared a note and an escalation to L2",
  ]) {
    await expect(pane.getByText(label, { exact: true })).toBeVisible();
  }
  await pane.getByText("Looked up the customer").click();
  await expect(pane.getByText("Ananya Kulkarni, premium customer.")).toBeVisible();
});

test("approving escalates the ticket to L2 and says who approved", async ({ page }) => {
  const card = await investigate(page);
  await card.getByRole("button", { name: "Approve" }).click();
  const pane = ticketPane(page);
  await expect(pane.getByText("Approved by Priya Nair")).toBeVisible();
  await expect(pane.getByRole("heading", { name: "Summary" })).toBeVisible();
  await expect(pane.getByText("Note added and ticket escalated to L2.")).toBeVisible();
  await expect(approvalCard(page)).toBeHidden();
  const proposed = pane.getByRole("heading", { name: "Proposed changes" }).locator("..");
  await expect(proposed.getByText("Done", { exact: true })).toBeVisible();
  await expect(pane.getByRole("heading", { name: "Reply to the customer" })).toBeHidden();
  await expect(pane.getByText(`#${TICKETS.stuck} · Order 4471 · P2 · L2 queue`)).toBeVisible();
});

test("rejecting asks for a reason and changes nothing", async ({ page }) => {
  const card = await investigate(page);
  await card.getByRole("button", { name: "Reject" }).click();
  const reason = card.getByRole("textbox", { name: "Reason for rejecting" });
  await expect(reason).toBeFocused();
  await expect(card.getByRole("button", { name: "Reject" })).toBeDisabled();
  await reason.fill("The customer asked to wait one more day.");
  await card.getByRole("button", { name: "Reject" }).click();
  const pane = ticketPane(page);
  await expect(pane.getByText("Rejected by Priya Nair")).toBeVisible();
  await expect(pane.getByText("Nothing was changed.")).toBeVisible();
  const proposed = pane.getByRole("heading", { name: "Proposed changes" }).locator("..");
  await expect(proposed.getByText("Rejected", { exact: true })).toBeVisible();
  await expect(pane.getByText(`#${TICKETS.stuck} · Order 4471 · P2 · L1 queue`)).toBeVisible();
  await expect(pane.getByRole("button", { name: "Investigate with Copilot" })).toBeVisible();
});

test("backing out of a rejection returns to the decision", async ({ page }) => {
  const card = await investigate(page);
  await card.getByRole("button", { name: "Reject" }).click();
  await card.getByRole("button", { name: "Back" }).click();
  await expect(card.getByRole("button", { name: "Approve" })).toBeVisible();
});

test("the list marks the ticket as needing this person's approval", async ({ page }) => {
  await investigate(page);
  await page.goto("/inbox?view=my_queue");
  await expect(
    page.getByRole("link", { name: /Order stuck for four days/ }).getByText("Needs your approval"),
  ).toBeVisible();
});

test("a follow-up question is answered in the same conversation", async ({ page }) => {
  await send(page, "Has anything changed on this order?");
  const pane = ticketPane(page);
  await expect(pane.getByText("Has anything changed on this order?")).toBeVisible();
  await expect(pane.getByText(/Order 4471 is still packed/)).toBeVisible();
  await expect(pane.getByRole("heading", { name: "Summary" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Message" })).toHaveValue("");
});

test("an empty message is not sent", async ({ page, fake }) => {
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Type a message first.")).toBeVisible();
  expect(await fake.seen((entry) => entry.method === "POST" && entry.path === "/api/runs")).toEqual(
    [],
  );
});

test("Enter sends and Shift+Enter makes a new line", async ({ page }) => {
  const box = page.getByRole("textbox", { name: "Message" });
  await box.fill("First line");
  await box.press("Shift+Enter");
  await expect(box).toHaveValue("First line\n");
  await box.pressSequentially("second line");
  await box.press("Enter");
  await expect(ticketPane(page).getByText(/Order 4471 is still packed/)).toBeVisible();
});

test("a running investigation can be stopped", async ({ page, fake }) => {
  await fake.set({ pace_ms: 400 });
  await page.getByRole("button", { name: "Investigate with Copilot" }).click();
  await page.getByRole("button", { name: "Stop" }).click();
  const pane = ticketPane(page);
  await expect(pane.getByRole("heading", { name: "Investigation stopped" })).toBeVisible();
  await expect(pane.getByText("You stopped this investigation.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Send" })).toBeVisible();
});

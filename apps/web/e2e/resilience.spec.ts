import {
  alerts,
  approvalCard,
  investigate,
  isPhone,
  openTicket,
  send,
  signIn,
  ticketList,
  ticketPane,
} from "./support/app";
import { TICKETS } from "./support/story";
import { expect, test } from "./support/test";

const RUN = "/api/runs/[0-9a-f-]{36}";
const RECONNECT_MS = 20_000;
const BUSY_MS = 20_000;

test("the run is followed again from the last event, with nothing missing or twice", async ({
  page,
  fake,
}) => {
  await fake.set({ pace_ms: 250, drop_stream_after: 6 });
  await signIn(page, "priya");
  await openTicket(page, TICKETS.stuck);
  await page.getByRole("button", { name: "Investigate with Copilot" }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "Connection lost. Reconnecting" }),
  ).toHaveText("Connection lost. Reconnecting, and nothing will be missed.");

  await expect(approvalCard(page)).toBeVisible({ timeout: RECONNECT_MS });
  await expect(page.getByText("Connection lost.")).toBeHidden();
  const pane = ticketPane(page);
  await pane.getByText(/^6 steps/).click();
  for (const label of [
    "Checked order 4471",
    "Looked up the customer",
    "Read the ticket notes",
    "Searched the runbooks",
    "Read the refund policy",
    "Prepared a note and an escalation to L2",
  ]) {
    await expect(pane.getByText(label, { exact: true })).toHaveCount(1);
  }
  await expect(pane.getByText(/I will check order 4471/)).toHaveCount(1);

  const streams = await fake.seen((entry) => entry.path.endsWith("/events"));
  // The browser names the last event it received whole, which may be one short of what was sent.
  expect(streams.map((entry) => entry.last_event_id)).toEqual([
    null,
    expect.stringMatching(/^[1-6]$/),
  ]);
});

test("a refused call asks the person to sign in again, and work goes on after", async ({
  page,
  fake,
}) => {
  await signIn(page, "priya");
  await page.goto("/inbox?view=my_queue");
  await expect(ticketList(page).getByRole("link", { name: /Order stuck/ })).toBeVisible();
  // The agent refuses every read of the list from here on, as it does once a token has run out.
  await fake.fault({ method: "GET", path: "/api/tickets", status: 401, times: 50 });
  await page.goto("/inbox?view=done");

  const dialog = page.getByRole("dialog", { name: "Sign in again" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText(/Your sign-in has ended\. Nothing was lost/)).toBeVisible();
  // The dialog must be answered: Escape does not put it away.
  await page.keyboard.press("Escape");
  await expect(dialog).toBeVisible();
  await fake.clearFaults();
  await dialog.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/sign-in$/);
  await page.getByRole("button", { name: /Priya Nair/ }).click();
  await page.waitForURL(/\/inbox/);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.goto("/inbox?view=done");
  await expect(ticketList(page).getByRole("link", { name: /Paid but not shipped/ })).toBeVisible();
});

test("an event stream that cannot be opened for that reason asks the same", async ({
  page,
  fake,
}) => {
  await signIn(page, "priya");
  await openTicket(page, TICKETS.stuck);
  await fake.fault({ method: "GET", path: `${RUN}/events`, status: 401 });
  await fake.fault({ method: "GET", path: RUN, status: 401 });
  await page.getByRole("button", { name: "Investigate with Copilot" }).click();
  await expect(page.getByRole("dialog", { name: "Sign in again" })).toBeVisible();
});

test("a run that pauses because the sign-in ran out asks the person to sign in again", async ({
  page,
  fake,
}) => {
  await fake.set({ pause_for_reauth: true });
  await signIn(page, "priya");
  await openTicket(page, TICKETS.stuck);
  await page.getByRole("button", { name: "Investigate with Copilot" }).click();
  await expect(page.getByRole("dialog", { name: "Sign in again" })).toBeVisible();
  await expect(approvalCard(page)).toHaveCount(0);
});

test("a run that paused for sign-in goes on once its owner is back", async ({ page, fake }) => {
  await fake.set({ pause_for_reauth: true });
  await signIn(page, "priya");
  await openTicket(page, TICKETS.stuck);
  await page.getByRole("button", { name: "Investigate with Copilot" }).click();
  const dialog = page.getByRole("dialog", { name: "Sign in again" });
  await dialog.getByRole("button", { name: "Sign in" }).click();
  await page.getByRole("button", { name: /Priya Nair/ }).click();
  await page.waitForURL(/\/inbox/);

  await openTicket(page, TICKETS.stuck);
  const card = approvalCard(page);
  await expect(card).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await card.getByRole("button", { name: "Approve" }).click();
  await expect(ticketPane(page).getByText("Approved by Priya Nair")).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("a page asked for with a session the agent refuses goes to sign-in", async ({
  page,
  fake,
}) => {
  await signIn(page, "priya");
  await fake.fault({ method: "GET", path: "/api/me", status: 401 });
  await page.goto("/inbox");
  await expect(page).toHaveURL(/\/sign-in$/);
});

test("too many requests is waited out and asked again by itself", async ({ page, fake }) => {
  await signIn(page, "priya");
  const path = `/api/tickets/${TICKETS.exception}`;
  await fake.fault({ method: "GET", path, status: 429, retry_after_s: 1 });
  await page.goto(`/inbox/${TICKETS.exception}?view=my_queue`);
  await expect(ticketPane(page).getByRole("heading", { level: 1 })).toHaveText(
    "Tracking shows a carrier exception",
    { timeout: BUSY_MS },
  );
  await expect(alerts(page)).toHaveCount(0);
  expect(await fake.seen((entry) => entry.path === path)).toHaveLength(2);
});

test("a message refused as too many says so and can be sent again", async ({ page, fake }) => {
  await signIn(page, "priya");
  await openTicket(page, TICKETS.stuck);
  await fake.fault({ method: "POST", path: "/api/runs", status: 429, retry_after_s: 1 });
  await send(page, "Has anything changed on this order?");
  await expect(alerts(page)).toHaveText("Too many requests just now.");
  await expect(page.getByRole("textbox", { name: "Message" })).toHaveValue(
    "Has anything changed on this order?",
  );
  await page.getByRole("button", { name: "Send" }).click();
  await expect(ticketPane(page).getByText(/Order 4471 is still packed/)).toBeVisible();
  await expect(alerts(page)).toHaveCount(0);
});

test("a list that cannot be read says what failed and offers to try again", async ({
  page,
  fake,
}) => {
  await signIn(page, "priya");
  await page.goto("/inbox?view=my_queue");
  await expect(ticketList(page).getByRole("link", { name: /Order stuck/ })).toBeVisible();
  // Every read of the list fails, however many the page makes, until the fault is lifted.
  await fake.fault({ method: "GET", path: "/api/tickets", status: 503, times: 50 });
  await page.goto("/inbox?view=done");
  const alert = ticketList(page).getByRole("alert");
  await expect(alert).toContainText("Meridian's systems are not answering right now.");
  await fake.clearFaults();
  await alert.getByRole("button", { name: "Try again" }).click();
  await expect(ticketList(page).getByRole("link", { name: /Paid but not shipped/ })).toBeVisible();
});

test("a step that could not be done is named above the composer", async ({ page, fake }) => {
  await fake.set({ runbooks_down: true });
  await signIn(page, "priya");
  await openTicket(page, TICKETS.stuck);
  await investigate(page);
  await expect(page.getByRole("status").filter({ hasText: "not answering" })).toHaveText(
    "Runbook search is not answering right now. Copilot went on without.",
  );
  await expect(ticketPane(page).getByText(/^6 steps · .* · 1 issue$/)).toBeVisible();
});

test("a slow answer shows the shape of the page, then the page", async ({ page, fake }) => {
  await signIn(page, "priya");
  const path = `/api/tickets/${TICKETS.exception}`;
  await fake.fault({ method: "GET", path, delay_ms: 1500 });
  await page.goto(`/inbox/${TICKETS.exception}?view=my_queue`);
  if (!isPhone(page)) {
    await expect(ticketList(page).getByRole("link", { name: /Tracking shows/ })).toBeVisible();
  }
  await expect(ticketPane(page)).toHaveCount(0);
  await expect(ticketPane(page).getByRole("heading", { level: 1 })).toBeVisible();
});

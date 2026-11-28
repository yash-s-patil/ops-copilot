import { alerts, isPhone, send, signIn } from "./support/app";
import { expect, test } from "./support/test";

const RUN_URL = /\/ask\/[0-9a-f-]{36}$/;

test("a question about an order is answered without a ticket", async ({ page }) => {
  await signIn(page, "priya");
  await page.goto("/ask");
  const chat = page.getByRole("region", { name: "Chat", exact: true });
  await expect(chat.getByText("Not tied to a ticket")).toBeVisible();
  await expect(chat.getByText(/Ask about any order by number\./)).toBeVisible();
  await send(page, "What is wrong with order 4471?");
  await expect(page).toHaveURL(RUN_URL);
  await expect(chat.getByText("What is wrong with order 4471?")).toBeVisible();
  await expect(chat.getByText(/^Order 4471: The order has been packed for 96 hours/)).toBeVisible();
  await expect(chat.getByRole("heading", { name: "Summary" })).toBeVisible();
  await expect(chat.getByText("Nothing was changed.")).toBeVisible();
  // No ticket, so no details beside the conversation.
  await expect(page.getByRole("complementary", { name: "Ticket details" })).toHaveCount(0);
});

test("an order of another region cannot be found", async ({ page }) => {
  await signIn(page, "rohan");
  await page.goto("/ask");
  await send(page, "What is wrong with order 4471?");
  const chat = page.getByRole("region", { name: "Chat", exact: true });
  await expect(chat.getByText(/I cannot find order 4471/)).toBeVisible();
  await expect(chat.getByText("packed for 96 hours")).toHaveCount(0);
});

test("the person's own chats are listed, and a new one can be started", async ({ page }) => {
  await signIn(page, "priya");
  await page.goto("/ask");
  await send(page, "What is wrong with order 4471?");
  await expect(page).toHaveURL(RUN_URL);
  const chat = page.getByRole("region", { name: "Chat", exact: true });
  await expect(chat.getByRole("heading", { name: "Summary" })).toBeVisible();
  if (isPhone(page)) await page.getByRole("link", { name: "Your chats" }).click();
  const chats = page.getByRole("region", { name: "Chats", exact: true });
  const row = chats.getByRole("link", { name: /What is wrong with order 4471\?/ });
  await expect(row).toContainText("Answered");
  await chats.getByRole("link", { name: "New chat" }).click();
  await expect(page).toHaveURL(/\/ask$/);
  await expect(chat.getByText(/Ask about any order by number\./)).toBeVisible();
});

test("with no chats yet the list says what will appear there", async ({ page }) => {
  await signIn(page, "priya");
  await page.goto(isPhone(page) ? "/ask?chats" : "/ask");
  await expect(
    page.getByText("Questions that are not tied to a ticket appear here."),
  ).toBeVisible();
});

test("another person's chat does not exist for this one", async ({ page, fake }) => {
  const started = await fake.post("arjun", "/api/runs", {
    data: { message: "What is wrong with order 4471?" },
  });
  const body: unknown = await started.json();
  const runId =
    typeof body === "object" && body !== null && "run_id" in body ? String(body.run_id) : "";
  await signIn(page, "priya");
  await page.goto(`/ask/${runId}`);
  await expect(alerts(page)).toHaveText("Nothing here is visible to you.");
});

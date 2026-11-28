import { signIn } from "./support/app";
import { TICKETS } from "./support/story";
import { expect, test } from "./support/test";

const LIST_REFRESH_MS = 25_000;
const CUSTOMER_DATA = /Ananya|Kulkarni|example\.in|I will check order|packed for 96 hours/;

test("an administrator sees Operations and no engineer screens", async ({ page }) => {
  await signIn(page, "sam");
  const nav = page.getByRole("navigation", { name: "Main" });
  await expect(nav.getByRole("link")).toHaveCount(2);
  await expect(nav.locator('a[href="/admin/live"]')).toHaveAttribute("aria-current", "page");
  await expect(nav.locator('a[href="/admin/runs"]')).toBeVisible();
  await expect(nav.locator('a[href="/inbox"], a[href="/ask"]')).toHaveCount(0);
  for (const path of ["/inbox", `/inbox/${TICKETS.stuck}`, "/ask"]) {
    await page.goto(path);
    await expect(page).toHaveURL(/\/admin\/live$/);
  }
});

test("an administrator's calls for tickets are refused by the server", async ({ page }) => {
  await signIn(page, "sam");
  const response = await page.request.get("/api/bff/tickets?view=all_open");
  expect(response.status()).toBe(403);
});

test("with no runs yet, both pages say so", async ({ page }) => {
  await signIn(page, "sam");
  await expect(page.getByText("No runs yet.")).toBeVisible();
  await page.goto("/admin/runs");
  await expect(page.getByRole("cell", { name: "No runs yet." })).toBeVisible();
});

test("the live trace follows the latest run without showing what it was about", async ({
  page,
  fake,
}) => {
  await signIn(page, "sam");
  await fake.investigationWaits();
  const trace = page.getByRole("region", { name: "Live trace" });
  await expect(trace.getByText("Priya Nair")).toBeVisible();
  // Who ran it and how it stands come with the list of runs, which is read every 15 seconds.
  await expect(trace.getByText("waiting for approval")).toBeVisible({ timeout: LIST_REFRESH_MS });
  await expect(trace.getByText("6 / 15")).toBeVisible();
  await expect(trace.getByText("get_order").first()).toBeVisible();
  await expect(trace.getByText("approval required").first()).toBeVisible();
  await expect(trace).not.toContainText(CUSTOMER_DATA);

  await trace.getByRole("checkbox", { name: /raw event stream/ }).check();
  await expect(trace.locator("pre")).toContainText("event: tool.finished");
  await expect(trace.locator("pre")).not.toContainText(CUSTOMER_DATA);
});

test("Runs lists every run and opens one as a trace", async ({ page, fake }) => {
  await fake.investigationWaits();
  await signIn(page, "sam");
  await page.goto("/admin/runs");
  const runs = page.getByRole("region", { name: "Runs" });
  const row = runs.getByRole("row").filter({ hasText: "Priya Nair" });
  await expect(row).toContainText("L1");
  await expect(row).toContainText("waiting for approval");
  await row.getByRole("link").click();
  await expect(page).toHaveURL(/\/admin\/runs\/[0-9a-f-]{36}$/);
  await expect(runs.getByText("search_runbooks").first()).toBeVisible();
  await expect(runs).not.toContainText(CUSTOMER_DATA);
  await runs.getByRole("link", { name: "All runs" }).click();
  await expect(page).toHaveURL(/\/admin\/runs$/);
});

test("an engineer cannot open the admin area", async ({ page }) => {
  await signIn(page, "priya");
  for (const path of ["/admin", "/admin/live", "/admin/runs"]) {
    await page.goto(path);
    await expect(page).toHaveURL(/\/inbox/);
  }
  await expect(
    page.getByRole("navigation", { name: "Main" }).locator('a[href^="/admin"]'),
  ).toHaveCount(0);
  const response = await page.request.get("/api/bff/admin/runs");
  expect(response.status()).toBe(403);
});

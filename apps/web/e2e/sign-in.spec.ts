import { signIn } from "./support/app";
import { USERS } from "./support/story";
import { expect, test } from "./support/test";

test.describe("signing in and out", () => {
  test("a visitor who is not signed in is sent to sign in", async ({ page }) => {
    await page.goto("/inbox");
    await expect(page).toHaveURL(/\/sign-in$/);
    await expect(page.getByRole("heading", { name: "Ops Copilot" })).toBeVisible();
    for (const name of Object.values(USERS)) {
      await expect(page.getByRole("button", { name: new RegExp(name) })).toBeVisible();
    }
  });

  test("an engineer signs in and lands in the inbox as themselves", async ({ page }) => {
    await signIn(page, "priya");
    await expect(page.getByRole("heading", { name: "Inbox" })).toBeVisible();
    await expect(
      page.getByRole("button", { name: /^Priya Nair.*theme and sign out$/ }),
    ).toBeVisible();
  });

  test("an administrator signs in and lands on the live trace", async ({ page }) => {
    await signIn(page, "sam");
    await expect(page.getByRole("heading", { name: "Live trace" })).toBeVisible();
  });

  test("signing out ends the session", async ({ page }) => {
    await signIn(page, "priya");
    await page.getByRole("button", { name: /^Priya Nair.*theme and sign out$/ }).click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/sign-in$/);
    await page.goto("/inbox");
    await expect(page).toHaveURL(/\/sign-in$/);
  });

  test("a call made without a session is refused, not passed on", async ({ page, fake }) => {
    await page.goto("/sign-in");
    const response = await page.request.get("/api/bff/tickets?view=my_queue");
    expect(response.status()).toBe(401);
    expect(await fake.seen((entry) => entry.path.startsWith("/api/tickets"))).toEqual([]);
  });
});

import { investigate, isPhone, openTicket, signIn, ticketPane } from "./support/app";
import { TICKETS } from "./support/story";
import { expect, test } from "./support/test";

test.describe("the Technical details switch", () => {
  test("technical lines appear only while it is on", async ({ page }) => {
    await signIn(page, "priya");
    await openTicket(page, TICKETS.stuck);
    await investigate(page);
    const pane = ticketPane(page);
    const toggle = page.getByRole("switch", { name: "Technical details" });
    const hash = pane.getByText(/update_ticket · args hash [0-9a-f]{12} · [0-9a-f-]{36}/);
    const footer = pane.getByText(/6 tool calls · [\d,]+ tokens · .* · run [0-9a-f]{8}/);

    await expect(toggle).not.toBeChecked();
    await expect(hash).toHaveCount(0);
    await expect(footer).toHaveCount(0);
    await expect(pane).not.toContainText(/get_order|tokens|args hash/);

    await toggle.click();
    await expect(toggle).toBeChecked();
    await expect(hash).toBeVisible();
    await expect(footer).toBeVisible();
    await pane.getByText(/^6 steps/).click();
    // The step's own technical words need room, so a phone leaves them to the expanded step.
    if (!isPhone(page)) await expect(pane.getByText(/get_order · \d+ ms · \d+ B/)).toBeVisible();
    await pane.getByText("Checked order 4471").click();
    await expect(pane.locator("pre")).toContainText('"order_id": 4471');

    await toggle.click();
    await expect(hash).toHaveCount(0);
    await expect(footer).toHaveCount(0);
    await expect(pane.locator("pre")).toHaveCount(0);
    await expect(pane).not.toContainText(/get_order|tokens|args hash/);
  });

  test("an administrator is not offered the switch", async ({ page }) => {
    await signIn(page, "sam");
    await expect(page.getByRole("switch", { name: "Technical details" })).toHaveCount(0);
  });
});

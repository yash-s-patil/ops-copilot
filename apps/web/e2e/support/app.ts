/** How a person moves through the application, at whichever width the test runs. */
import AxeBuilder from "@axe-core/playwright";
import { expect, type Locator, type Page } from "@playwright/test";
import { USERS, type User } from "./story";

const ONE_PANE_BELOW = 720;
const DRAWER_BELOW = 1280;

function widthOf(page: Page): number {
  return page.viewportSize()?.width ?? 0;
}

/** Below 720 px the list and the ticket take turns on the one screen. */
export function isPhone(page: Page): boolean {
  return widthOf(page) < ONE_PANE_BELOW;
}

/** Below 1280 px the details are a drawer and the rail shows icons only. */
export function hasDrawer(page: Page): boolean {
  return widthOf(page) < DRAWER_BELOW;
}

export async function signIn(page: Page, user: User): Promise<void> {
  await page.goto("/sign-in");
  await page.getByRole("button", { name: new RegExp(USERS[user]) }).click();
  await page.waitForURL(user === "sam" ? /\/admin\/live/ : /\/inbox/);
}

/** Another person takes the same browser: the first one's session is gone, as after sign-out. */
export async function switchTo(page: Page, user: User): Promise<void> {
  await page.context().clearCookies({ name: /authjs/ });
  await signIn(page, user);
}

export function ticketPane(page: Page): Locator {
  return page.getByRole("region", { name: "Ticket", exact: true });
}

export function ticketList(page: Page): Locator {
  return page.getByRole("region", { name: "Tickets", exact: true });
}

export async function openTicket(page: Page, ticketId: number, view = "all_open"): Promise<void> {
  await page.goto(`/inbox/${ticketId}?view=${view}`);
  await expect(ticketPane(page).getByRole("heading", { level: 1 })).toBeVisible();
}

export function detailsPane(page: Page): Locator {
  return page.getByRole("complementary", { name: "Ticket details" });
}

/** The details, opened first where they are a drawer. */
export async function openDetails(page: Page): Promise<Locator> {
  const pane = detailsPane(page);
  if (hasDrawer(page) && !(await pane.isVisible())) {
    await page.getByRole("button", { name: "Ticket details" }).click();
  }
  await expect(pane).toBeVisible();
  return pane;
}

export async function closeDetails(page: Page): Promise<void> {
  if (!hasDrawer(page)) return;
  await page.keyboard.press("Escape");
  await expect(detailsPane(page)).toBeHidden();
}

/** What the application says went wrong. Next.js keeps an announcer of its own in the same role. */
export function alerts(page: Page): Locator {
  return page.locator('[role="alert"]:not(#__next-route-announcer__)');
}

export function approvalCard(page: Page): Locator {
  return page.getByRole("group", { name: "Approve these changes" });
}

/** Starts an investigation and waits until it asks for a decision. */
export async function investigate(page: Page): Promise<Locator> {
  await page.getByRole("button", { name: "Investigate with Copilot" }).click();
  const card = approvalCard(page);
  await expect(card).toBeVisible();
  return card;
}

export async function send(page: Page, message: string): Promise<void> {
  await page.getByRole("textbox", { name: "Message" }).fill(message);
  await page.getByRole("button", { name: "Send" }).click();
}

export async function setTheme(page: Page, theme: "light" | "dark"): Promise<void> {
  const { origin } = new URL(page.url());
  await page.context().addCookies([{ name: "theme", value: theme, url: origin }]);
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
}

/** SPEC 8.4: the page body never scrolls sideways, at any width. */
export async function expectNoSideScroll(page: Page): Promise<void> {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow, "the page is wider than the window").toBeLessThanOrEqual(0);
}

export type Violation = { rule: string; where: string };

export async function violationsOn(page: Page): Promise<Violation[]> {
  const { violations } = await new AxeBuilder({ page }).analyze();
  return violations.map((violation) => ({
    rule: violation.id,
    where: violation.nodes.map((node) => node.target.join(" ")).join(" | "),
  }));
}

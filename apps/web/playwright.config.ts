import { randomBytes } from "node:crypto";
import { resolve } from "node:path";
import { defineConfig, devices } from "@playwright/test";
import { FAKE_AGENT_PORT, FAKE_AGENT_URL, WEB_PORT, WEB_URL } from "./e2e/support/addresses";

const CI = Boolean(process.env["CI"]);
const SECRET_BYTES = 32;
const BUILD_AND_START_MS = 300_000;

// Made for this one run and given to both servers. Nothing here is kept or worth keeping.
const once = () => randomBytes(SECRET_BYTES).toString("hex");
const WEB_CLIENT = { WEB_CLIENT_ID: "opscp-web-e2e", WEB_CLIENT_SECRET: once() };

/** The widths of SPEC 8.4: phone, drawer with icon rail, and the two full layouts. */
const WIDTHS = [
  { name: "390", viewport: { width: 390, height: 844 } },
  { name: "1024", viewport: { width: 1024, height: 768 } },
  { name: "1280", viewport: { width: 1280, height: 800 } },
  { name: "1440", viewport: { width: 1440, height: 900 } },
];

export default defineConfig({
  testDir: "./e2e",
  testMatch: "*.spec.ts",
  // The fake agent holds one story in memory, so tests take turns with it.
  fullyParallel: false,
  workers: 1,
  forbidOnly: CI,
  retries: CI ? 2 : 0,
  reporter: CI ? [["github"], ["html", { open: "never" }]] : "list",
  timeout: 45_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: WEB_URL,
    trace: "on-first-retry",
  },
  projects: WIDTHS.map(({ name, viewport }) => ({
    name,
    use: { ...devices["Desktop Chrome"], viewport },
  })),
  webServer: [
    {
      command: "node --import ./e2e/fake-agent/register.mjs e2e/fake-agent/server.ts",
      url: `${FAKE_AGENT_URL}/__test/health`,
      reuseExistingServer: !CI,
      env: { FAKE_AGENT_PORT: String(FAKE_AGENT_PORT) },
    },
    {
      command: `pnpm exec next build && pnpm exec next start --port ${WEB_PORT}`,
      url: `${WEB_URL}/healthz`,
      reuseExistingServer: !CI,
      timeout: BUILD_AND_START_MS,
      env: {
        ...WEB_CLIENT,
        ENVIRONMENT: "local",
        AUTH_MODE: "dev",
        AUTH_TRUST_HOST: "true",
        AUTH_SECRET: once(),
        AGENT_URL: FAKE_AGENT_URL,
        AGENT_SCOPE: "api://opscp-agent-e2e/Agent.Access",
        DEV_TOKEN_URL: `${FAKE_AGENT_URL}/token`,
        LIMITS_FILE: resolve(__dirname, "../../config/limits.yaml"),
      },
    },
  ],
});

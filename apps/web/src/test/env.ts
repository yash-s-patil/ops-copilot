import { randomBytes } from "node:crypto";
import { fileURLToPath } from "node:url";
import { vi } from "vitest";

export const LIMITS_FILE = fileURLToPath(
  new URL("../../../../config/limits.yaml", import.meta.url),
);
export const AGENT_URL = "http://agent.test:8001";

/** A value long enough to pass for a secret, made anew for each run of the tests. */
export function longRandom(): string {
  return randomBytes(24).toString("hex");
}

type Env = Record<string, string | undefined>;

const UNSET: Env = {
  AUTH_MICROSOFT_ENTRA_ID_ID: undefined,
  AUTH_MICROSOFT_ENTRA_ID_SECRET: undefined,
  AUTH_MICROSOFT_ENTRA_ID_ISSUER: undefined,
  DEV_TOKEN_URL: undefined,
  WEB_CLIENT_ID: undefined,
  WEB_CLIENT_SECRET: undefined,
};

function stub(env: Env): void {
  for (const [name, value] of Object.entries(env)) vi.stubEnv(name, value);
}

/** The settings of a developer's machine: the local stand-in for sign-in, a plain-http agent. */
export function stubLocalEnv(over: Env = {}): void {
  stub({
    ...UNSET,
    ENVIRONMENT: "local",
    AUTH_MODE: "dev",
    AUTH_SECRET: longRandom(),
    AGENT_URL,
    AGENT_SCOPE: "api://agent/Agent.Access",
    LIMITS_FILE,
    DEV_TOKEN_URL: "http://issuer.test:8090/token",
    WEB_CLIENT_ID: "web",
    WEB_CLIENT_SECRET: longRandom(),
    ...over,
  });
}

/** The settings of a deployed environment: Entra sign-in and an https agent. */
export function stubDeployedEnv(over: Env = {}): void {
  stub({
    ...UNSET,
    ENVIRONMENT: "prod",
    AUTH_MODE: "entra",
    AUTH_SECRET: longRandom(),
    AGENT_URL: "https://agent.internal.test",
    AGENT_SCOPE: "api://agent/Agent.Access",
    LIMITS_FILE,
    AUTH_MICROSOFT_ENTRA_ID_ID: "web-client",
    AUTH_MICROSOFT_ENTRA_ID_SECRET: longRandom(),
    AUTH_MICROSOFT_ENTRA_ID_ISSUER: "https://login.test/tenant/v2.0",
    ...over,
  });
}

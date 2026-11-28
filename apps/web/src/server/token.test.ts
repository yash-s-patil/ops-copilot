import { beforeEach, describe, expect, it, vi } from "vitest";
import { longRandom, stubLocalEnv } from "@/test/env";
import { inAnHour, SECURE_SESSION_COOKIE, sessionCookie, type Cookie } from "@/test/session-cookie";

const jar = vi.hoisted((): { cookies: { name: string; value: string }[] } => ({ cookies: [] }));

vi.mock("next/headers", () => ({
  headers: () =>
    Promise.resolve(
      new Headers({ cookie: jar.cookies.map(({ name, value }) => `${name}=${value}`).join("; ") }),
    ),
}));

let secret: string;
let forAgent: string;

beforeEach(() => {
  vi.resetModules();
  secret = longRandom();
  forAgent = longRandom();
  stubLocalEnv({ AUTH_SECRET: secret });
  jar.cookies = [];
});

async function tokenWith(...cookies: Cookie[]): Promise<string | null> {
  jar.cookies = cookies;
  const { accessToken } = await import("@/server/token");
  return accessToken();
}

describe("the signed-in person's token for the agent", () => {
  it("is read from the session cookie", async () => {
    const cookie = await sessionCookie(secret, { accessToken: forAgent, expiresAt: inAnHour() });
    expect(await tokenWith(cookie)).toBe(forAgent);
  });

  it("is read from the secure cookie a deployed site sets", async () => {
    const claims = { accessToken: forAgent, expiresAt: inAnHour() };
    const cookie = await sessionCookie(secret, claims, SECURE_SESSION_COOKIE);
    expect(await tokenWith({ name: "theme", value: "dark" }, cookie)).toBe(forAgent);
  });

  it("is null when there is no cookie at all", async () => {
    expect(await tokenWith()).toBeNull();
  });

  it("is null when the cookie was made with another secret", async () => {
    const claims = { accessToken: forAgent, expiresAt: inAnHour() };
    expect(await tokenWith(await sessionCookie(longRandom(), claims))).toBeNull();
  });

  it("is null when the cookie is not a session at all", async () => {
    expect(await tokenWith({ name: "authjs.session-token", value: "not-a-session" })).toBeNull();
  });

  it("is null once the token has run out, and at the very second it does", async () => {
    const nowS = Math.floor(Date.now() / 1000);
    for (const expiresAt of [nowS - 60, nowS]) {
      const cookie = await sessionCookie(secret, { accessToken: forAgent, expiresAt });
      expect(await tokenWith(cookie)).toBeNull();
    }
  });

  it("is null when the session does not say when the token runs out", async () => {
    expect(await tokenWith(await sessionCookie(secret, { accessToken: forAgent }))).toBeNull();
  });

  it("is null when the session holds no token", async () => {
    expect(await tokenWith(await sessionCookie(secret, { expiresAt: inAnHour() }))).toBeNull();
  });

  it("is null when the token could not be renewed", async () => {
    const claims = {
      accessToken: forAgent,
      expiresAt: inAnHour(),
      error: "RefreshTokenError",
    } as const;
    expect(await tokenWith(await sessionCookie(secret, claims))).toBeNull();
  });
});

import { beforeEach, describe, expect, it, vi } from "vitest";
import { longRandom, stubDeployedEnv, stubLocalEnv } from "@/test/env";

beforeEach(() => {
  vi.resetModules();
});

/** The module keeps what it read, so each test loads it anew. */
async function load() {
  return import("@/server/settings");
}

async function refusal(): Promise<string> {
  const { settings } = await load();
  try {
    settings();
  } catch (error) {
    if (error instanceof Error) return error.message;
  }
  throw new Error("the settings were accepted");
}

describe("settings that are accepted", () => {
  it("reads a developer's machine, where sign-in is the local stand-in", async () => {
    stubLocalEnv();
    const { settings, isLocal } = await load();
    expect(settings()).toMatchObject({ ENVIRONMENT: "local", AUTH_MODE: "dev" });
    expect(isLocal()).toBe(true);
  });

  it.each(["demo", "prod"])("reads %s with Entra sign-in and an https agent", async (name) => {
    stubDeployedEnv({ ENVIRONMENT: name });
    const { settings, isLocal } = await load();
    expect(settings().AUTH_MODE).toBe("entra");
    expect(isLocal()).toBe(false);
  });

  it("allows Entra sign-in on a developer's machine too", async () => {
    stubDeployedEnv({ ENVIRONMENT: "local", AGENT_URL: "http://127.0.0.1:8001" });
    expect((await load()).settings().ENVIRONMENT).toBe("local");
  });

  it("reads the environment once and keeps what it read", async () => {
    stubLocalEnv();
    const { settings } = await load();
    const first = settings();
    vi.stubEnv("ENVIRONMENT", "prod");
    expect(settings()).toBe(first);
  });
});

describe("settings that stop the application from starting", () => {
  it.each(["demo", "prod"])("refuses the local stand-in for sign-in in %s", async (name) => {
    stubLocalEnv({ ENVIRONMENT: name, AGENT_URL: "https://agent.internal.test" });
    expect(await refusal()).toContain("AUTH_MODE=dev is allowed only when ENVIRONMENT is local");
  });

  it.each(["demo", "prod"])("refuses an agent address that is not https in %s", async (name) => {
    stubDeployedEnv({ ENVIRONMENT: name, AGENT_URL: "http://agent.internal.test" });
    expect(await refusal()).toContain("AGENT_URL must be https outside local");
  });

  it("refuses a session secret shorter than 32 characters, without repeating it", async () => {
    const short = longRandom().slice(0, 31);
    stubLocalEnv({ AUTH_SECRET: short });
    const message = await refusal();
    expect(message).toContain("AUTH_SECRET");
    expect(message).not.toContain(short);
  });

  it("accepts a session secret of exactly 32 characters", async () => {
    stubLocalEnv({ AUTH_SECRET: longRandom().slice(0, 32) });
    expect((await load()).settings().AUTH_SECRET).toHaveLength(32);
  });

  it.each(["AUTH_SECRET", "AGENT_URL", "AGENT_SCOPE", "LIMITS_FILE", "ENVIRONMENT", "AUTH_MODE"])(
    "names %s when it is missing",
    async (name) => {
      stubLocalEnv({ [name]: undefined });
      expect(await refusal()).toContain(name);
    },
  );
});

describe("settings each kind of sign-in needs", () => {
  it.each(["DEV_TOKEN_URL", "WEB_CLIENT_ID", "WEB_CLIENT_SECRET"])(
    "says the local stand-in needs %s",
    async (name) => {
      stubLocalEnv({ [name]: undefined });
      expect(await refusal()).toContain(`AUTH_MODE=dev needs ${name}`);
    },
  );

  it.each([
    "AUTH_MICROSOFT_ENTRA_ID_ID",
    "AUTH_MICROSOFT_ENTRA_ID_SECRET",
    "AUTH_MICROSOFT_ENTRA_ID_ISSUER",
  ])("says Entra sign-in needs %s", async (name) => {
    stubDeployedEnv({ [name]: undefined });
    expect(await refusal()).toContain(`AUTH_MODE=entra needs ${name}`);
  });

  it("treats a setting given as nothing as missing", async () => {
    stubDeployedEnv({ AUTH_MICROSOFT_ENTRA_ID_ID: "" });
    expect(await refusal()).toContain("AUTH_MODE=entra needs AUTH_MICROSOFT_ENTRA_ID_ID");
  });

  it("refuses a client secret shorter than 32 characters, without repeating it", async () => {
    const short = longRandom().slice(0, 12);
    stubLocalEnv({ WEB_CLIENT_SECRET: short });
    const message = await refusal();
    expect(message).toContain("WEB_CLIENT_SECRET");
    expect(message).not.toContain(short);
  });
});

describe("settings that are not what they should be", () => {
  it.each([
    ["ENVIRONMENT", "staging"],
    ["AUTH_MODE", "none"],
    ["AGENT_URL", "not a url"],
    ["DEV_TOKEN_URL", "not a url"],
  ])("refuses %s=%j", async (name, value) => {
    stubLocalEnv({ [name]: value });
    expect(await refusal()).toContain(name);
  });

  it("names every problem at once and says the application cannot start", async () => {
    stubLocalEnv({ AUTH_SECRET: undefined, AGENT_SCOPE: undefined });
    const message = await refusal();
    expect(message).toMatch(/^The web application cannot start\./);
    expect(message).toContain("AUTH_SECRET");
    expect(message).toContain("AGENT_SCOPE");
  });

  it("refuses again each time it is asked, rather than serving with bad settings", async () => {
    stubLocalEnv({ AUTH_SECRET: undefined });
    const { settings } = await load();
    expect(() => settings()).toThrow();
    expect(() => settings()).toThrow();
  });
});

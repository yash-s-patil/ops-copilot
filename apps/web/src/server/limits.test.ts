import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { parse } from "yaml";
import { z } from "zod";
import { LIMITS_FILE, stubLocalEnv } from "@/test/env";

beforeEach(() => {
  vi.resetModules();
});

async function load() {
  return import("@/server/limits");
}

function fileWith(text: string): string {
  const path = join(mkdtempSync(join(tmpdir(), "limits-")), "limits.yaml");
  writeFileSync(path, text);
  return path;
}

const VALID = `
ui:
  inbox_refresh_s: 15
  refund_poll_s: 3
  refund_poll_max_duration_s: 120
  retries: 1
  retry_wait_ms: 1000
  busy_retries: 3
  busy_wait_s: 5
  reconnect_max_s: 15
runs: { body_bytes_max: 16384 }
user: { message_chars: 2000 }
approval: { decision_reason_chars: 300 }
budget: { tokens: 60000, tool_calls: 12 }
`;

const numbers = z.record(z.string(), z.record(z.string(), z.unknown()));

/** One value of the repository's limits file, read here apart from the code under test. */
function inRepo(section: string, name: string): number {
  const value = numbers.parse(parse(readFileSync(LIMITS_FILE, "utf8")))[section]?.[name];
  return z.number().parse(value);
}

describe("the repository's limits file", () => {
  it("gives the screens their intervals in milliseconds", async () => {
    stubLocalEnv();
    const limits = (await load()).uiLimits();
    expect(limits.inboxRefreshMs).toBe(inRepo("ui", "inbox_refresh_s") * 1000);
    expect(limits.refundPollMs).toBe(inRepo("ui", "refund_poll_s") * 1000);
    expect(limits.refundPollMaxMs).toBe(inRepo("ui", "refund_poll_max_duration_s") * 1000);
  });

  it("gives the screens the sizes of a message, a reason and a run's budget", async () => {
    stubLocalEnv();
    expect((await load()).uiLimits()).toMatchObject({
      messageChars: inRepo("user", "message_chars"),
      reasonChars: inRepo("approval", "decision_reason_chars"),
      runTokens: inRepo("budget", "tokens"),
      runToolCalls: inRepo("budget", "tool_calls"),
    });
  });

  it("gives the largest body a request may carry", async () => {
    stubLocalEnv();
    expect((await load()).bodyBytesMax()).toBe(inRepo("runs", "body_bytes_max"));
  });
});

describe("another limits file", () => {
  it("is read from where the settings say", async () => {
    stubLocalEnv({ LIMITS_FILE: fileWith(VALID.replace("15", "40")) });
    expect((await load()).uiLimits().inboxRefreshMs).toBe(40_000);
  });

  it("is read once", async () => {
    const path = fileWith(VALID);
    stubLocalEnv({ LIMITS_FILE: path });
    const { bodyBytesMax } = await load();
    expect(bodyBytesMax()).toBe(16_384);
    writeFileSync(path, VALID.replace("16384", "1"));
    expect(bodyBytesMax()).toBe(16_384);
  });

  it.each([
    ["a missing section", VALID.replace(/^runs:.*$/m, "")],
    ["a missing value", VALID.replace("  refund_poll_s: 3\n", "")],
    ["a value of zero", VALID.replace("body_bytes_max: 16384", "body_bytes_max: 0")],
    ["a negative value", VALID.replace("tool_calls: 12", "tool_calls: -1")],
    ["a value written as text", VALID.replace("message_chars: 2000", 'message_chars: "2000"')],
    ["an empty file", ""],
    ["a list instead of sections", "- ui\n- runs\n"],
    ["text that is not YAML", "ui: { inbox_refresh_s: [\n"],
  ])("is refused when it has %s", async (_what, text) => {
    stubLocalEnv({ LIMITS_FILE: fileWith(text) });
    const { uiLimits, bodyBytesMax } = await load();
    expect(() => uiLimits()).toThrow();
    expect(() => bodyBytesMax()).toThrow();
  });

  it("is refused when it does not exist", async () => {
    stubLocalEnv({ LIMITS_FILE: join(tmpdir(), "no-such-dir", "limits.yaml") });
    await expect(load().then(({ uiLimits }) => uiLimits())).rejects.toThrow();
  });
});

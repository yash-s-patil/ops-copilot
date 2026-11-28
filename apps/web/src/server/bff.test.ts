import { describe, expect, it } from "vitest";
import { allowed, FORWARDED_HEADERS, sameOrigin } from "@/server/bff";
import { ACTION_ID, RUN_ID } from "@/test/fixtures";

const NO_QUERY = new URLSearchParams();

function route(method: string, path: string, query = "") {
  return allowed(method, path.split("/"), new URLSearchParams(query));
}

const LISTED = [
  ["GET", "me", false],
  ["GET", "tickets", false],
  ["GET", "tickets/2207", false],
  ["POST", "tickets/2207/resolve", false],
  ["POST", "refunds/31/retry", false],
  ["POST", "runs", false],
  ["GET", "runs", false],
  ["GET", `runs/${RUN_ID}`, false],
  ["GET", `runs/${RUN_ID}/events`, true],
  ["GET", `runs/${RUN_ID}/calls/call_Ab-12`, false],
  ["POST", `runs/${RUN_ID}/actions/${ACTION_ID}/approve`, false],
  ["POST", `runs/${RUN_ID}/actions/${ACTION_ID}/reject`, false],
  ["POST", `runs/${RUN_ID}/cancel`, false],
  ["POST", `approvals/${ACTION_ID}/approve`, false],
  ["POST", `approvals/${ACTION_ID}/reject`, false],
  ["POST", `approvals/${ACTION_ID}/execute`, false],
  ["GET", "admin/runs", false],
  ["GET", "admin/runs/latest/events", true],
  ["GET", `admin/runs/${RUN_ID}`, false],
] as const;

describe("allowed: the listed routes", () => {
  it.each(LISTED)("passes %s %s on to the same path under /api", (method, path, stream) => {
    expect(route(method, path)).toEqual({ upstream: `/api/${path}`, stream });
  });

  it.each(LISTED)("refuses %s %s with the other method", (method, path) => {
    const other = method === "GET" ? "POST" : "GET";
    const both = LISTED.some(([m, p]) => m === other && p === path);
    if (!both) expect(route(other, path)).toBeNull();
  });

  it.each(LISTED)("refuses %s %s with a segment added or taken away", (method, path) => {
    expect(route(method, `${path}/extra`)?.upstream ?? null).not.toBe(`/api/${path}/extra`);
    expect(route(method, `x/${path}`)).toBeNull();
    expect(route(method, `${path}/`)).toBeNull();
  });
});

describe("allowed: everything else", () => {
  it.each(["PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "get", "post", ""])(
    "refuses the method %j on any path",
    (method) => {
      for (const [, path] of LISTED) expect(route(method, path)).toBeNull();
    },
  );
});

describe("allowed: unlisted and malformed paths", () => {
  it.each([
    "",
    "healthz",
    "admin",
    "approvals",
    `approvals/${ACTION_ID}`,
    `approvals/${ACTION_ID}/token`,
    `runs/${RUN_ID}/actions/${ACTION_ID}`,
    `runs/${RUN_ID}/actions/${ACTION_ID}/execute`,
    "admin/runs/latest",
    "tickets/2207/notes",
    "refunds/31",
    "ME",
    "me.json",
  ])("refuses the unlisted path %j with either method", (path) => {
    expect(route("GET", path)).toBeNull();
    expect(route("POST", path)).toBeNull();
  });

  it.each([
    ["a way up out of the path", ["tickets", "..", "me"]],
    ["a way up inside a segment", ["tickets", "2207/../../me"]],
    ["dots as a ticket number", ["tickets", ".."]],
    ["an encoded slash", ["tickets", "2207%2Fresolve"]],
    ["an encoded way up", ["tickets", "%2e%2e", "me"]],
    ["a doubly encoded slash", ["tickets", "2207%252Fresolve"]],
    ["a backslash", ["tickets", "2207\\resolve"]],
    ["an empty segment", ["tickets", "", "2207"]],
    ["a line break after the path", ["me\n"]],
    ["a null byte", ["me\u0000"]],
    ["a query written into the path", ["tickets?view=done"]],
    ["a fragment written into the path", ["me#x"]],
  ])("refuses %s", (_what, segments) => {
    expect(allowed("GET", segments, NO_QUERY)).toBeNull();
    expect(allowed("POST", segments, NO_QUERY)).toBeNull();
  });
});

describe("allowed: identifiers", () => {
  it.each([
    ["a ticket number that is not digits", "tickets/22o7"],
    ["a negative ticket number", "tickets/-1"],
    ["a ticket number of seventeen digits", `tickets/${"9".repeat(17)}`],
    ["a run that is not a UUID", "runs/latest"],
    ["a run in capital letters", `runs/${RUN_ID.toUpperCase()}`],
    ["a run with a space after it", `runs/${RUN_ID} `],
    ["a call name with a dot", `runs/${RUN_ID}/calls/a.b`],
    ["a call name of sixty-five characters", `runs/${RUN_ID}/calls/${"a".repeat(65)}`],
  ])("refuses %s", (_what, path) => {
    expect(route("GET", path)).toBeNull();
  });

  it("accepts a ticket number of sixteen digits and a call name of sixty-four characters", () => {
    expect(route("GET", `tickets/${"9".repeat(16)}`)).not.toBeNull();
    expect(route("GET", `runs/${RUN_ID}/calls/${"a".repeat(64)}`)).not.toBeNull();
  });

  it("treats a slash that arrives inside a segment as the path it spells", () => {
    expect(allowed("POST", ["tickets", "2207/resolve"], NO_QUERY)).toEqual({
      upstream: "/api/tickets/2207/resolve",
      stream: false,
    });
  });
});

describe("allowed: the query", () => {
  it("passes on the names listed for the route", () => {
    expect(route("GET", "tickets", "view=waiting&cursor=abc")?.upstream).toBe(
      "/api/tickets?view=waiting&cursor=abc",
    );
    expect(route("GET", "tickets/2207", "part=status")?.upstream).toBe(
      "/api/tickets/2207?part=status",
    );
    expect(route("GET", "runs", "ask=true")?.upstream).toBe("/api/runs?ask=true");
  });

  it("drops every name that is not listed for the route", () => {
    expect(route("GET", "tickets", "view=done&as_user=kavita&limit=9999")?.upstream).toBe(
      "/api/tickets?view=done",
    );
    expect(route("GET", "me", "debug=1")?.upstream).toBe("/api/me");
    expect(route("POST", "runs", "ask=true")?.upstream).toBe("/api/runs");
    expect(route("GET", "tickets/2207", "view=done")?.upstream).toBe("/api/tickets/2207");
  });

  it("takes the first value when a name is given twice", () => {
    expect(route("GET", "tickets", "view=waiting&view=done")?.upstream).toBe(
      "/api/tickets?view=waiting",
    );
  });

  it("encodes a value again, so that it cannot add a name of its own", () => {
    const smuggled = `cursor=${encodeURIComponent("a&as_user=kavita#x")}`;
    expect(route("GET", "tickets", smuggled)?.upstream).toBe(
      "/api/tickets?cursor=a%26as_user%3Dkavita%23x",
    );
  });

  it("keeps a name given with no value", () => {
    expect(route("GET", "tickets", "view=")?.upstream).toBe("/api/tickets?view=");
  });
});

describe("the headers passed on", () => {
  it("are three, and neither the cookie nor an authorization is among them", () => {
    expect([...FORWARDED_HEADERS].sort()).toEqual([
      "content-type",
      "idempotency-key",
      "last-event-id",
    ]);
  });
});

describe("sameOrigin", () => {
  it("accepts a request from this application's own pages", () => {
    expect(sameOrigin("https://copilot.test", "copilot.test")).toBe(true);
    expect(sameOrigin("http://localhost:3000", "localhost:3000")).toBe(true);
  });

  it("does not compare the scheme, since TLS ends before this server", () => {
    expect(sameOrigin("https://copilot.test", "copilot.test")).toBe(
      sameOrigin("http://copilot.test", "copilot.test"),
    );
  });

  it.each([
    ["another site", "https://evil.test", "copilot.test"],
    ["another port", "http://localhost:4000", "localhost:3000"],
    ["a site that begins with this one's name", "https://copilot.test.evil.test", "copilot.test"],
    ["a subdomain", "https://x.copilot.test", "copilot.test"],
    ["this site's name as a user name", "https://copilot.test@evil.test", "copilot.test"],
    ["the origin a sandboxed page sends", "null", "copilot.test"],
    ["an origin that is not an address", "copilot.test", "copilot.test"],
    ["an empty origin", "", "copilot.test"],
  ])("refuses %s", (_what, origin, host) => {
    expect(sameOrigin(origin, host)).toBe(false);
  });

  it("refuses a request that names no origin or no host", () => {
    expect(sameOrigin(null, "copilot.test")).toBe(false);
    expect(sameOrigin("https://copilot.test", null)).toBe(false);
    expect(sameOrigin(null, null)).toBe(false);
  });
});

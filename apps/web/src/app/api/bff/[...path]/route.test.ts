import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AGENT_URL, longRandom, stubLocalEnv } from "@/test/env";
import { answerJson, answerText, fakeFetch, sent, type FetchMock } from "@/test/fetch";
import { ACTION_ID, RUN_ID } from "@/test/fixtures";
import { inAnHour, sessionCookie } from "@/test/session-cookie";

const jar = vi.hoisted((): { cookies: { name: string; value: string }[] } => ({ cookies: [] }));

vi.mock("next/headers", () => ({
  headers: () =>
    Promise.resolve(
      new Headers({ cookie: jar.cookies.map(({ name, value }) => `${name}=${value}`).join("; ") }),
    ),
}));

const SITE = "http://copilot.test";
const HOST = "copilot.test";

let fetchMock: FetchMock;
let secret: string;
let forAgent: string;

beforeEach(() => {
  vi.resetModules();
  secret = longRandom();
  forAgent = longRandom();
  stubLocalEnv({ AUTH_SECRET: secret });
  jar.cookies = [];
  fetchMock = fakeFetch();
  answerJson(fetchMock, { ok: true });
});

async function signIn(): Promise<void> {
  jar.cookies = [await sessionCookie(secret, { accessToken: forAgent, expiresAt: inAnHour() })];
}

type Ask = {
  method?: "GET" | "POST";
  headers?: Record<string, string>;
  body?: string;
  undeclared?: boolean;
};

/** Calls the route as Next does: the request, and the path after /api/bff as segments. */
async function ask(path: string, { method = "GET", headers = {}, body, undeclared }: Ask = {}) {
  const route = await import("./route");
  const [pathname = ""] = path.split("?");
  const sending = new Headers({ host: HOST });
  if (method === "POST") sending.set("origin", SITE);
  if (body !== undefined && !undeclared) {
    sending.set("content-length", String(Buffer.byteLength(body)));
  }
  for (const [name, value] of Object.entries(headers)) sending.set(name, value);
  const request = new NextRequest(`${SITE}/api/bff/${path}`, {
    method,
    headers: sending,
    body,
  });
  const context = { params: Promise.resolve({ path: pathname.split("/") }) };
  return method === "POST" ? route.POST(request, context) : route.GET(request, context);
}

describe("who may call", () => {
  it("refuses with 401 when nobody is signed in, and asks the agent nothing", async () => {
    const response = await ask("me");
    expect(response.status).toBe(401);
    expect(await response.json()).toMatchObject({ code: "SIGNED_OUT" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not take a request's own Authorization header for a session", async () => {
    const response = await ask("me", { headers: { authorization: `Bearer ${longRandom()}` } });
    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("answers 404 for a route that is not listed, signed in or not", async () => {
    expect((await ask("approvals")).status).toBe(404);
    await signIn();
    const response = await ask(`approvals/${ACTION_ID}/token`, { method: "POST" });
    expect(response.status).toBe(404);
    expect(await response.json()).toMatchObject({ code: "NOT_FOUND" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    ["another site", { origin: "http://evil.test" }],
    ["no origin at all", { origin: "" }],
  ])("refuses a change that comes from %s with 403", async (_what, headers) => {
    await signIn();
    const response = await ask(`runs/${RUN_ID}/cancel`, { method: "POST", headers });
    expect(response.status).toBe(403);
    expect(await response.json()).toMatchObject({ code: "PERMISSION_DENIED" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not ask where a read came from", async () => {
    await signIn();
    expect((await ask("me", { headers: { origin: "http://evil.test" } })).status).toBe(200);
  });
});

describe("what is passed to the agent", () => {
  beforeEach(signIn);

  it("is the listed path at the agent's address, with the person's token from the cookie", async () => {
    await ask("tickets?view=waiting&as_user=kavita");
    const request = sent(fetchMock);
    expect(request.url).toBe(`${AGENT_URL}/api/tickets?view=waiting`);
    expect(request.method).toBe("GET");
    expect(request.headers.get("authorization")).toBe(`Bearer ${forAgent}`);
    expect(request.init.cache).toBe("no-store");
    expect(request.body).toBeNull();
  });

  it("is only the content type, the idempotency key and the last event seen", async () => {
    const click = longRandom();
    const headers = {
      "content-type": "application/json",
      "idempotency-key": click,
      "last-event-id": "12",
      authorization: `Bearer ${longRandom()}`,
      cookie: "authjs.session-token=from-the-browser; theme=dark",
      "x-forwarded-for": "203.0.113.9",
      "user-agent": "a browser",
      accept: "text/event-stream",
    };
    await ask("tickets/2207/resolve", { method: "POST", headers });
    const passed = sent(fetchMock).headers;
    expect([...passed.keys()].sort()).toEqual([
      "authorization",
      "content-type",
      "idempotency-key",
      "last-event-id",
    ]);
    expect(passed.get("authorization")).toBe(`Bearer ${forAgent}`);
    expect(passed.get("idempotency-key")).toBe(click);
  });

  it("never includes the cookie", async () => {
    await ask("me", { headers: { cookie: "authjs.session-token=from-the-browser" } });
    expect(sent(fetchMock).headers.has("cookie")).toBe(false);
  });
});

describe("the body of a change", () => {
  beforeEach(signIn);

  it("carries the body of a change as it was sent", async () => {
    const body = JSON.stringify({ message: "Why is order 4471 late?", ticket_id: 2207 });
    await ask("runs", { method: "POST", body, headers: { "content-type": "application/json" } });
    expect(sent(fetchMock)).toMatchObject({ url: `${AGENT_URL}/api/runs`, method: "POST", body });
  });

  it("sends no body for a change that has none", async () => {
    await ask(`runs/${RUN_ID}/cancel`, { method: "POST" });
    expect(sent(fetchMock).body).toBeNull();
  });

  it("refuses a body that declares more than the limit, before reading it", async () => {
    const { bodyBytesMax } = await import("@/server/limits");
    const over = String(bodyBytesMax() + 1);
    const response = await ask("runs", { method: "POST", headers: { "content-length": over } });
    expect(response.status).toBe(413);
    expect(await response.json()).toMatchObject({ code: "VALIDATION_FAILED" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("accepts a body of exactly the limit", async () => {
    const { bodyBytesMax } = await import("@/server/limits");
    const response = await ask("runs", { method: "POST", body: "x".repeat(bodyBytesMax()) });
    expect(response.status).toBe(200);
  });

  it("refuses a body whose declared length is not a number", async () => {
    const headers = { "content-length": "plenty" };
    expect((await ask("runs", { method: "POST", headers })).status).toBe(413);
  });

  it("refuses a body over the limit that declares no length", async () => {
    const { bodyBytesMax } = await import("@/server/limits");
    const body = "x".repeat(bodyBytesMax() + 1);
    const response = await ask("runs", { method: "POST", body, undeclared: true });
    expect(response.status).toBe(413);
  });
});

describe("what comes back", () => {
  beforeEach(signIn);

  it("is the agent's status and body, as JSON that is never cached", async () => {
    answerJson(
      fetchMock,
      { code: "PERMISSION_DENIED", message: "Not for your role." },
      { status: 403 },
    );
    const response = await ask("tickets/2207");
    expect(response.status).toBe(403);
    expect(response.headers.get("content-type")).toBe("application/json");
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.json()).toMatchObject({ code: "PERMISSION_DENIED" });
  });

  it("tells the browser how long to wait when the agent says so", async () => {
    answerJson(
      fetchMock,
      { code: "RATE_LIMITED", message: "Slow down." },
      {
        status: 429,
        headers: { "Retry-After": "7" },
      },
    );
    const response = await ask("tickets");
    expect(response.status).toBe(429);
    expect(response.headers.get("retry-after")).toBe("7");
  });

  it("leaves behind every other header of the agent's", async () => {
    answerJson(fetchMock, {}, { headers: { "x-internal-host": "agent-7f9", "set-cookie": "a=b" } });
    const response = await ask("me");
    expect(response.headers.has("x-internal-host")).toBe(false);
    expect(response.headers.has("set-cookie")).toBe(false);
    expect(response.headers.has("retry-after")).toBe(false);
  });
});

describe("a stream, and an agent that cannot be reached", () => {
  beforeEach(signIn);

  it("is a stream of events for a run's events, not held back on the way", async () => {
    const events = "id: 1\nevent: run.started\ndata: {}\n\n";
    answerText(fetchMock, events, { headers: { "content-type": "text/event-stream" } });
    const response = await ask(`runs/${RUN_ID}/events`, { headers: { "last-event-id": "0" } });
    expect(response.headers.get("content-type")).toBe("text/event-stream");
    expect(response.headers.get("cache-control")).toBe("no-cache, no-transform");
    expect(response.headers.get("x-accel-buffering")).toBe("no");
    expect(await response.text()).toBe(events);
    expect(sent(fetchMock).headers.get("last-event-id")).toBe("0");
  });

  it("is a plain refusal, not a stream, when the agent refuses to stream", async () => {
    answerJson(fetchMock, { code: "NOT_FOUND", message: "No such run." }, { status: 404 });
    const response = await ask(`runs/${RUN_ID}/events`);
    expect(response.status).toBe(404);
    expect(response.headers.get("content-type")).toBe("application/json");
  });

  it("says Meridian's systems are not answering when the agent cannot be reached", async () => {
    fetchMock.mockRejectedValue(new TypeError(`connect ECONNREFUSED ${AGENT_URL}`));
    const response = await ask("me");
    expect(response.status).toBe(503);
    const text = await response.text();
    expect(JSON.parse(text)).toMatchObject({ code: "BACKEND_UNAVAILABLE" });
    expect(text).not.toContain("agent.test");
    expect(text).not.toContain("ECONNREFUSED");
  });
});

import { beforeEach, describe, expect, it } from "vitest";
import { ADMIN_LIVE_URL, api, ApiError, eventsUrl } from "@/lib/api";
import { answerJson, answerText, fakeFetch, sent, type FetchMock } from "@/test/fetch";
import { ACTION_ID, ARGS_HASH, PRIYA, RUN_ID, TICKET_ID } from "@/test/fixtures";

let fetchMock: FetchMock;

beforeEach(() => {
  fetchMock = fakeFetch();
});

const RUN = {
  run_id: RUN_ID,
  ticket_id: TICKET_ID,
  order_id: 4471,
  status: "running",
  message: "Why is this order late?",
  started_at: "2026-11-28T09:00:00Z",
  finished_at: null,
  result: null,
  stop_reason: null,
};
const DECIDED = { action_id: ACTION_ID, decision: "approved", resumed: true };
const COUNTS = { my_queue: 0, all_open: 0, waiting: 0, done: 0 };

/** Each call the browser can make: what answers it, and the request it must send. */
const CALLS = [
  ["me", () => api.me(), PRIYA, "GET", "/me", null],
  [
    "tickets",
    () => api.tickets("waiting"),
    { items: [], next_cursor: null, counts: COUNTS },
    "GET",
    "/tickets?view=waiting",
    null,
  ],
  [
    "ticketStatus",
    () => api.ticketStatus(TICKET_ID),
    { ticket_id: TICKET_ID, status: "open", queue: "L1", refund_state: null, version: 3 },
    "GET",
    "/tickets/2207?part=status",
    null,
  ],
  ["retryRefund", () => api.retryRefund(31), {}, "POST", "/refunds/31/retry", null],
  [
    "startRun on a ticket",
    () => api.startRun("Why is this late?", TICKET_ID),
    { run_id: RUN_ID },
    "POST",
    "/runs",
    { message: "Why is this late?", ticket_id: TICKET_ID },
  ],
  [
    "startRun with no ticket",
    () => api.startRun("What is the refund policy?", null),
    { run_id: RUN_ID },
    "POST",
    "/runs",
    { message: "What is the refund policy?" },
  ],
  ["runs", () => api.runs(false), { items: [] }, "GET", "/runs", null],
  ["runs asked from Ask", () => api.runs(true), { items: [RUN] }, "GET", "/runs?ask=true", null],
  ["run", () => api.run(RUN_ID), RUN, "GET", `/runs/${RUN_ID}`, null],
  [
    "approve",
    () => api.approve(RUN_ID, ACTION_ID, ARGS_HASH),
    DECIDED,
    "POST",
    `/runs/${RUN_ID}/actions/${ACTION_ID}/approve`,
    { args_hash: ARGS_HASH },
  ],
  [
    "reject",
    () => api.reject(RUN_ID, ACTION_ID, "Wrong order."),
    DECIDED,
    "POST",
    `/runs/${RUN_ID}/actions/${ACTION_ID}/reject`,
    { reason: "Wrong order." },
  ],
  ["cancel", () => api.cancel(RUN_ID), { stopped: true }, "POST", `/runs/${RUN_ID}/cancel`, null],
  [
    "supervisorApprove",
    () => api.supervisorApprove(ACTION_ID, ARGS_HASH),
    {},
    "POST",
    `/approvals/${ACTION_ID}/approve`,
    { args_hash: ARGS_HASH },
  ],
  [
    "supervisorReject",
    () => api.supervisorReject(ACTION_ID, "Over the limit."),
    {},
    "POST",
    `/approvals/${ACTION_ID}/reject`,
    { reason: "Over the limit." },
  ],
  [
    "execute",
    () => api.execute(ACTION_ID),
    { action_id: ACTION_ID, resumed_run: null },
    "POST",
    `/approvals/${ACTION_ID}/execute`,
    null,
  ],
  [
    "callDetail",
    () => api.callDetail(RUN_ID, "c1"),
    { call_id: "c1", tool: "get_order", args: {}, result: null },
    "GET",
    `/runs/${RUN_ID}/calls/c1`,
    null,
  ],
  ["adminRuns", () => api.adminRuns(), { items: [] }, "GET", "/admin/runs", null],
] as const;

describe("every call", () => {
  it.each(CALLS)(
    "%s goes to this application's own server",
    async (_name, call, answer, ...want) => {
      const [method, path, body] = want;
      answerJson(fetchMock, answer);
      await call();
      const request = sent(fetchMock);
      expect(request.url).toBe(`/api/bff${path}`);
      expect(request.method).toBe(method);
      expect(request.body === null ? null : JSON.parse(request.body)).toEqual(body);
      expect(request.init.cache).toBe("no-store");
    },
  );

  it.each(CALLS)("%s carries no credentials of its own", async (_name, call, answer) => {
    answerJson(fetchMock, answer);
    await call();
    const { headers, init } = sent(fetchMock);
    expect(init.credentials).toBeUndefined();
    const allowed = ["content-type", "idempotency-key"];
    expect([...headers.keys()].filter((name) => !allowed.includes(name))).toEqual([]);
  });

  it.each(CALLS)("%s says its body is JSON only when it sends one", async (_name, call, answer) => {
    answerJson(fetchMock, answer);
    await call();
    const { headers, body } = sent(fetchMock);
    expect(headers.get("content-type")).toBe(body === null ? null : "application/json");
  });
});

describe("particular calls", () => {
  it("resolves a ticket with the key of the click and no body", async () => {
    const click = "click".repeat(6);
    answerJson(fetchMock, { ticket_id: TICKET_ID, status: "resolved", version: 4 });
    await expect(api.resolve(TICKET_ID, click)).resolves.toMatchObject({ status: "resolved" });
    const request = sent(fetchMock);
    expect(request.url).toBe("/api/bff/tickets/2207/resolve");
    expect(request.method).toBe("POST");
    expect(request.headers.get("idempotency-key")).toBe(click);
    expect(request.body).toBeNull();
  });

  it("sends no idempotency key with anything else", async () => {
    answerJson(fetchMock, {});
    await api.retryRefund(31);
    expect(sent(fetchMock).headers.has("idempotency-key")).toBe(false);
  });

  it("escapes a call's name so that it cannot reach another address", async () => {
    answerJson(fetchMock, { call_id: "x", tool: "get_order", args: {}, result: null });
    await api.callDetail(RUN_ID, "../../admin/runs?x=1");
    expect(sent(fetchMock).url).toBe(
      `/api/bff/runs/${RUN_ID}/calls/..%2F..%2Fadmin%2Fruns%3Fx%3D1`,
    );
  });

  it("knows where a run's events and the admin's live trace are streamed from", () => {
    expect(eventsUrl(RUN_ID)).toBe(`/api/bff/runs/${RUN_ID}/events`);
    expect(ADMIN_LIVE_URL).toBe("/api/bff/admin/runs/latest/events");
  });

  it("gives back what was parsed, without fields it was not told about", async () => {
    answerJson(fetchMock, { ...PRIYA, extra: "dropped" });
    await expect(api.me()).resolves.toEqual(PRIYA);
  });
});

describe("a refusal", () => {
  const failure = (call: Promise<unknown>) =>
    call.then(
      () => {
        throw new Error("the call should have failed");
      },
      (error: unknown) => {
        if (!(error instanceof ApiError)) throw error;
        return error;
      },
    );

  it("becomes an error with the code, message and details the server gave", async () => {
    const body = { code: "PERMISSION_DENIED", message: "Not for your role.", details: { a: 1 } };
    answerJson(fetchMock, body, { status: 403 });
    const error = await failure(api.me());
    expect(error).toMatchObject({ name: "ApiError", status: 403, ...body });
    expect(error.signedOut).toBe(false);
  });

  it("has empty details when the server gave none", async () => {
    answerJson(fetchMock, { code: "NOT_FOUND", message: "Nothing here." }, { status: 404 });
    expect((await failure(api.run(RUN_ID))).details).toEqual({});
  });

  it("says the person is signed out for a 401, whatever the body", async () => {
    answerJson(fetchMock, { code: "SIGNED_OUT", message: "Sign in again." }, { status: 401 });
    expect((await failure(api.me())).signedOut).toBe(true);
    answerText(fetchMock, "", { status: 401 });
    expect((await failure(api.me())).signedOut).toBe(true);
  });

  it.each([
    ["a page of HTML", "<html>Bad Gateway</html>"],
    ["nothing", ""],
    ["JSON of another shape", JSON.stringify({ detail: "Internal Server Error" })],
  ])("becomes a plain unknown error when the body is %s", async (_what, body) => {
    answerText(fetchMock, body, { status: 502 });
    const error = await failure(api.me());
    expect(error).toMatchObject({ status: 502, code: "UNKNOWN" });
    expect(error.message).toBe("Something went wrong. Try again.");
  });

  it("keeps the retry wait out of the message but keeps the code for a rate limit", async () => {
    const body = { code: "RATE_LIMITED", message: "Too many requests." };
    answerJson(fetchMock, body, { status: 429, headers: { "Retry-After": "7" } });
    expect(await failure(api.me())).toMatchObject({ status: 429, code: "RATE_LIMITED" });
  });
});

describe("a good status with a bad body", () => {
  it.each([
    ["not JSON", "<html></html>"],
    ["empty", ""],
    ["JSON of another shape", JSON.stringify({ name: 7 })],
  ])("is an unreadable answer when the body is %s", async (_what, body) => {
    answerText(fetchMock, body);
    await expect(api.me()).rejects.toMatchObject({
      name: "ApiError",
      code: "UNREADABLE",
      message: "The answer could not be read. Try again.",
    });
  });

  it("is still fine with no body where none is expected", async () => {
    answerText(fetchMock, "");
    await expect(api.cancel(RUN_ID)).resolves.toBeNull();
  });
});

describe("the server cannot be reached", () => {
  it("lets the failure through as it is", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(api.me()).rejects.toBeInstanceOf(TypeError);
  });
});

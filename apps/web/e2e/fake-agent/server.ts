/**
 * A stand-in for the agent and for the local sign-in issuer, for the end-to-end tests.
 * It keeps the demo story in memory and answers in the shapes the real services use.
 */
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { control, faultFor } from "./control";
import { jsonOf, Refusal, sendJson, sendRefusal, sendSignedOut, textOf } from "./http";
import { isUserKey, personOf, tokenFor, type UserKey } from "./people";
import { routeFor, STREAMED } from "./routes";
import { world, type Fault } from "./world";

const DEMO_GRANT = "urn:opscp:params:oauth:grant-type:demo-user";
const TOKEN_LIFETIME_S = 3600;
const DEFAULT_PORT = 8310;
const WEB_CLIENT_ID = "opscp-web-e2e";
const SECRET_CHARS_MIN = 32;
const SIGNED_OUT = 401;
const REAUTH = "reauth_required";
const BUSY = 429;

function oauthError(response: ServerResponse, error: string): void {
  sendJson(response, 400, { error, error_description: "The token request was refused." });
}

function noteSignIn(form: URLSearchParams): void {
  const user = form.get("user");
  const asked = new URLSearchParams({
    grant_type: form.get("grant_type") ?? "",
    client_id: form.get("client_id") ?? "",
  });
  world.journal.push({
    method: "POST",
    path: `/token?${asked.toString()}`,
    user: isUserKey(user) ? user : null,
    last_event_id: null,
    idempotency_key: null,
  });
}

/** The demo-user grant of the local issuer: the web application names a person, and gets a token. */
function token(response: ServerResponse, body: string): void {
  const form = new URLSearchParams(body);
  const user = form.get("user");
  noteSignIn(form);
  const secret = form.get("client_secret") ?? "";
  if (form.get("grant_type") !== DEMO_GRANT) return oauthError(response, "unsupported_grant_type");
  // Any secret of a real one's length will do: this server guards nothing.
  if (form.get("client_id") !== WEB_CLIENT_ID || secret.length < SECRET_CHARS_MIN) {
    return oauthError(response, "invalid_client");
  }
  if (!isUserKey(user)) return oauthError(response, "invalid_grant");
  world.sign_ins.set(user, (world.sign_ins.get(user) ?? 0) + 1);
  sendJson(response, 200, {
    token_type: "Bearer",
    access_token: tokenFor(user),
    expires_in: TOKEN_LIFETIME_S,
    scope: form.get("scope") ?? "",
  });
}

function wait(ms: number): Promise<void> {
  return new Promise((done) => setTimeout(done, ms));
}

/** True when the fault answered the request itself. */
async function met(fault: Fault | null, response: ServerResponse): Promise<boolean> {
  if (fault === null) return false;
  if (fault.delay_ms > 0) await wait(fault.delay_ms);
  if (fault.status === null) return false;
  if (fault.status === SIGNED_OUT) {
    sendSignedOut(response);
  } else if (fault.status === BUSY) {
    const details = { retry_after_seconds: fault.retry_after_s ?? 1 };
    sendRefusal(response, new Refusal("RATE_LIMITED", "Too many requests just now.", details));
  } else {
    const message = "Meridian's systems could not be reached.";
    sendJson(response, fault.status, { code: "BACKEND_UNAVAILABLE", message, details: {} });
  }
  return true;
}

function header(request: IncomingMessage, name: string): string | null {
  const value = request.headers[name];
  return typeof value === "string" ? value : null;
}

/**
 * A run pauses for sign-in when its owner's session has run out, so until they sign in
 * again every call they make is refused, as the real agent refuses an expired token.
 */
function sessionRanOut(user: UserKey): boolean {
  const signIns = world.sign_ins.get(user) ?? 0;
  return [...world.runs.values()].some(
    (run) => run.owner === user && run.status === REAUTH && signIns <= run.sign_ins_at_pause,
  );
}

async function api(request: IncomingMessage, response: ServerResponse, url: URL): Promise<void> {
  const method = request.method ?? "GET";
  const who = personOf(request.headers.authorization);
  world.journal.push({
    method,
    path: url.pathname + url.search,
    user: who?.key ?? null,
    last_event_id: header(request, "last-event-id"),
    idempotency_key: header(request, "idempotency-key"),
  });
  const body = await textOf(request);
  if (await met(faultFor(method, url.pathname), response)) return;
  if (who === null || sessionRanOut(who.key)) return sendSignedOut(response);
  const found = routeFor(method, url.pathname);
  if (found === null) throw new Refusal("NOT_FOUND", "There is nothing at this address.");
  const { route, ids } = found;
  const call = { who, ids, query: url.searchParams, body: jsonOf(body), request, response };
  const answer = route.run(call);
  if (answer !== STREAMED) sendJson(response, route.status ?? 200, answer);
}

async function handle(request: IncomingMessage, response: ServerResponse): Promise<void> {
  const url = new URL(request.url ?? "/", "http://fake-agent");
  const method = request.method ?? "GET";
  if (url.pathname.startsWith("/api/")) return api(request, response, url);
  const body = await textOf(request);
  if (method === "POST" && url.pathname === "/token") return token(response, body);
  const answer = control(method, url.pathname, jsonOf(body));
  if (answer === null) throw new Refusal("NOT_FOUND", "There is nothing at this address.");
  sendJson(response, 200, answer);
}

const server = createServer((request, response) => {
  handle(request, response).catch((error: unknown) => {
    if (response.headersSent) return response.destroy();
    if (error instanceof Refusal) return sendRefusal(response, error);
    sendJson(response, 500, { code: "INTERNAL", message: "The fake agent failed.", details: {} });
  });
});

server.listen(Number(process.env["FAKE_AGENT_PORT"] ?? DEFAULT_PORT), "127.0.0.1");

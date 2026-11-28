/**
 * What the browser may ask the agent for, through this server.
 *
 * The browser never reaches the agent. It calls this server, which attaches the signed-in
 * person's token and passes on only the routes, query names and headers listed here.
 */
import "server-only";

type Method = "GET" | "POST";
type Rule = { method: Method; path: RegExp; stream?: boolean; query?: readonly string[] };

const ID = "[0-9]{1,16}";
const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const CALL = "[A-Za-z0-9_-]{1,64}";

const RULES: readonly Rule[] = [
  { method: "GET", path: /^me$/ },
  { method: "GET", path: /^tickets$/, query: ["view", "cursor"] },
  { method: "GET", path: new RegExp(`^tickets/${ID}$`), query: ["part"] },
  { method: "POST", path: new RegExp(`^tickets/${ID}/resolve$`) },
  { method: "POST", path: new RegExp(`^refunds/${ID}/retry$`) },
  { method: "POST", path: /^runs$/ },
  { method: "GET", path: /^runs$/, query: ["ask"] },
  { method: "GET", path: new RegExp(`^runs/${UUID}$`) },
  { method: "GET", path: new RegExp(`^runs/${UUID}/events$`), stream: true },
  { method: "GET", path: new RegExp(`^runs/${UUID}/calls/${CALL}$`) },
  { method: "POST", path: new RegExp(`^runs/${UUID}/actions/${UUID}/(approve|reject)$`) },
  { method: "POST", path: new RegExp(`^runs/${UUID}/cancel$`) },
  { method: "POST", path: new RegExp(`^approvals/${UUID}/(approve|reject|execute)$`) },
  { method: "GET", path: /^admin\/runs$/ },
  { method: "GET", path: /^admin\/runs\/latest\/events$/, stream: true },
  { method: "GET", path: new RegExp(`^admin/runs/${UUID}$`) },
];

export const FORWARDED_HEADERS = ["content-type", "idempotency-key", "last-event-id"] as const;

export type Allowed = { upstream: string; stream: boolean };

/** The agent path a request may be passed to, or null when it is not one of the listed. */
export function allowed(
  method: string,
  segments: readonly string[],
  query: URLSearchParams,
): Allowed | null {
  const path = segments.join("/");
  const rule = RULES.find((candidate) => candidate.method === method && candidate.path.test(path));
  if (rule === undefined) return null;
  const kept = new URLSearchParams();
  for (const name of rule.query ?? []) {
    const value = query.get(name);
    if (value !== null) kept.set(name, value);
  }
  const suffix = kept.size === 0 ? "" : `?${kept.toString()}`;
  return { upstream: `/api/${path}${suffix}`, stream: rule.stream === true };
}

/**
 * A change must come from one of this application's own pages. The host is compared and
 * not the scheme: behind an ingress that ends TLS this server sees http whatever the
 * browser used.
 */
export function sameOrigin(origin: string | null, host: string | null): boolean {
  if (origin === null || host === null || !URL.canParse(origin)) return false;
  return new URL(origin).host === host;
}

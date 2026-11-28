import type { APIRequestContext, APIResponse } from "@playwright/test";
import { FAKE_AGENT_URL } from "./addresses";
import { INVESTIGATE, TICKETS, type User } from "./story";

const POLLS = 100;
const POLL_MS = 50;

export type Scene = "escalated" | "waiting_for_supervisor";

export type Switches = {
  pace_ms?: number;
  payment_step_ms?: number;
  drop_stream_after?: number;
  fail_next_refund?: boolean;
  runbooks_down?: boolean;
  pause_for_reauth?: boolean;
};

export type Fault = {
  method: "GET" | "POST";
  /** A pattern for the whole path, for example `/api/tickets/2207`. */
  path: string;
  status?: number;
  retry_after_s?: number;
  delay_ms?: number;
  times?: number;
};

export type Seen = {
  method: string;
  path: string;
  user: string | null;
  last_event_id: string | null;
  idempotency_key: string | null;
};

type Sent = { data?: unknown; headers?: Record<string, string> };

function isSeen(value: unknown): value is Seen {
  return typeof value === "object" && value !== null && "method" in value && "path" in value;
}

/** The fake names its user in the token and checks nothing else, so this is no credential. */
function bearer(user: User): Record<string, string> {
  const token = Buffer.from(`demo-user:${user}`).toString("base64url");
  return { Authorization: `Bearer ${token}` };
}

/** The test's own line to the fake agent: set the scene, flip a switch, read what it saw. */
export class FakeAgent {
  private readonly request: APIRequestContext;

  constructor(request: APIRequestContext) {
    this.request = request;
  }

  private async control(path: string, data: unknown): Promise<void> {
    const response = await this.request.post(`${FAKE_AGENT_URL}/__test/${path}`, { data });
    if (!response.ok()) throw new Error(`The fake agent refused ${path}: ${response.status()}`);
  }

  reset(...scenes: Scene[]): Promise<void> {
    return this.control("reset", { scenes });
  }

  set(switches: Switches): Promise<void> {
    return this.control("switches", switches);
  }

  fault(fault: Fault): Promise<void> {
    return this.control("faults", fault);
  }

  clearFaults(): Promise<void> {
    return this.control("faults/clear", {});
  }

  async seen(matching: (entry: Seen) => boolean = () => true): Promise<Seen[]> {
    const response = await this.request.get(`${FAKE_AGENT_URL}/__test/journal`);
    const body: unknown = await response.json();
    const items =
      typeof body === "object" && body !== null && "items" in body && Array.isArray(body.items)
        ? body.items
        : [];
    return items.filter(isSeen).filter(matching);
  }

  /** Priya's investigation of the stuck order, started past the browser and waiting for her. */
  async investigationWaits(): Promise<void> {
    const data = { message: INVESTIGATE, ticket_id: TICKETS.stuck };
    const known = await (await this.get("priya", "/api/runs")).text();
    if (!known.includes('"run_id"')) await this.post("priya", "/api/runs", { data });
    for (let attempt = 0; attempt < POLLS; attempt += 1) {
      const runs = await (await this.get("priya", "/api/runs")).text();
      if (runs.includes('"awaiting_approval"')) return;
      await new Promise((done) => setTimeout(done, POLL_MS));
    }
    throw new Error("The investigation never reached the approval");
  }

  /** A call straight to the agent API as one of the demo users, past the web application. */
  get(user: User, path: string, headers: Record<string, string> = {}): Promise<APIResponse> {
    return this.request.get(`${FAKE_AGENT_URL}${path}`, {
      headers: { ...bearer(user), ...headers },
    });
  }

  post(user: User, path: string, sent: Sent = {}): Promise<APIResponse> {
    return this.request.post(`${FAKE_AGENT_URL}${path}`, {
      headers: { ...bearer(user), ...sent.headers },
      data: sent.data,
    });
  }
}

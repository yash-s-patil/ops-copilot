/** Calls from the browser. Every one goes to this application's own server, never further. */
import type { z } from "zod";
import * as s from "@/lib/schemas";

const BFF = "/api/bff";
const SIGNED_OUT = 401;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }

  get signedOut(): boolean {
    return this.status === SIGNED_OUT;
  }
}

const UNREADABLE = "The answer could not be read. Try again.";

async function failure(response: Response): Promise<ApiError> {
  const body = s.errorBody.safeParse(await response.json().catch(() => null));
  if (!body.success) {
    return new ApiError(response.status, "UNKNOWN", "Something went wrong. Try again.");
  }
  return new ApiError(response.status, body.data.code, body.data.message, body.data.details);
}

type Options = { method?: "GET" | "POST"; body?: unknown; headers?: Record<string, string> };

async function call<T extends z.ZodType>(
  path: string,
  schema: T,
  options: Options = {},
): Promise<z.infer<T>> {
  const response = await fetch(BFF + path, {
    method: options.method ?? "GET",
    headers: {
      ...(options.body === undefined ? {} : { "Content-Type": "application/json" }),
      ...options.headers,
    },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    cache: "no-store",
  });
  if (!response.ok) throw await failure(response);
  const parsed = schema.safeParse(await response.json().catch(() => null));
  if (!parsed.success) throw new ApiError(response.status, "UNREADABLE", UNREADABLE);
  return parsed.data;
}

function post<T extends z.ZodType>(path: string, schema: T, body?: unknown): Promise<z.infer<T>> {
  return call(path, schema, { method: "POST", body });
}

export const api = {
  me: () => call("/me", s.me),
  tickets: (view: s.View) => call(`/tickets?view=${view}`, s.ticketPage),
  workspace: (ticketId: number) => call(`/tickets/${ticketId}`, s.workspace),
  ticketStatus: (ticketId: number) => call(`/tickets/${ticketId}?part=status`, s.ticketStatus),
  resolve: (ticketId: number, click: string) =>
    call(`/tickets/${ticketId}/resolve`, s.resolved, {
      method: "POST",
      headers: { "Idempotency-Key": click },
    }),
  retryRefund: (refundId: number) => post(`/refunds/${refundId}/retry`, s.anything),
  startRun: (message: string, ticketId: number | null) =>
    post("/runs", s.started, ticketId === null ? { message } : { message, ticket_id: ticketId }),
  runs: (askOnly: boolean) => call(`/runs${askOnly ? "?ask=true" : ""}`, s.runList),
  run: (runId: string) => call(`/runs/${runId}`, s.runView),
  approve: (runId: string, actionId: string, argsHash: string) =>
    post(`/runs/${runId}/actions/${actionId}/approve`, s.decided, { args_hash: argsHash }),
  reject: (runId: string, actionId: string, reason: string) =>
    post(`/runs/${runId}/actions/${actionId}/reject`, s.decided, { reason }),
  cancel: (runId: string) => post(`/runs/${runId}/cancel`, s.anything),
  supervisorApprove: (actionId: string, argsHash: string) =>
    post(`/approvals/${actionId}/approve`, s.anything, { args_hash: argsHash }),
  supervisorReject: (actionId: string, reason: string) =>
    post(`/approvals/${actionId}/reject`, s.anything, { reason }),
  execute: (actionId: string) => post(`/approvals/${actionId}/execute`, s.executed),
  callDetail: (runId: string, callId: string) =>
    call(`/runs/${runId}/calls/${encodeURIComponent(callId)}`, s.callDetail),
  adminRuns: () => call("/admin/runs", s.adminRuns),
  adminRun: (runId: string) => call(`/admin/runs/${runId}`, s.adminRunDetail),
};

export function eventsUrl(runId: string): string {
  return `${BFF}/runs/${runId}/events`;
}

export const ADMIN_LIVE_URL = `${BFF}/admin/runs/latest/events`;

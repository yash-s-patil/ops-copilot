/** Reading requests and writing answers, in the shapes the real agent uses. */
import type { IncomingMessage, ServerResponse } from "node:http";
import type { Json } from "./world";

const STATUS = {
  PERMISSION_DENIED: 403,
  NOT_FOUND: 404,
  POLICY_VIOLATION: 409,
  APPROVAL_INVALID: 409,
  VALIDATION_FAILED: 422,
  RATE_LIMITED: 429,
} as const;

export type Code = keyof typeof STATUS;

/** A refusal in the agent's one error body. */
export class Refusal extends Error {
  readonly status: number;
  readonly code: Code;
  readonly details: Json;

  constructor(code: Code, message: string, details: Json = {}) {
    super(message);
    this.code = code;
    this.status = STATUS[code];
    this.details = details;
  }
}

export const notFound = (message: string) => new Refusal("NOT_FOUND", message);
export const denied = () => new Refusal("PERMISSION_DENIED", "Your role does not allow this.");
export const invalid = (message: string) => new Refusal("VALIDATION_FAILED", message);

export function sendJson(
  response: ServerResponse,
  status: number,
  body: unknown,
  headers: Record<string, string> = {},
): void {
  response.writeHead(status, { "Content-Type": "application/json", ...headers });
  response.end(JSON.stringify(body));
}

export function sendRefusal(response: ServerResponse, refusal: Refusal): void {
  const retryAfter = refusal.details["retry_after_seconds"];
  sendJson(
    response,
    refusal.status,
    {
      code: refusal.code,
      message: refusal.message,
      retryable: refusal.code === "RATE_LIMITED",
      details: refusal.details,
    },
    typeof retryAfter === "number" ? { "Retry-After": String(retryAfter) } : {},
  );
}

/** The real agent answers a missing or expired token with no body at all. */
export function sendSignedOut(response: ServerResponse): void {
  response.writeHead(401, { "WWW-Authenticate": "Bearer" });
  response.end();
}

export async function textOf(request: IncomingMessage): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of request) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(String(chunk)));
  }
  return Buffer.concat(chunks).toString("utf8");
}

export function isJson(value: unknown): value is Json {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function jsonOf(text: string): Json {
  if (text === "") return {};
  try {
    const parsed: unknown = JSON.parse(text);
    if (isJson(parsed)) return parsed;
  } catch {
    // Answered below, like any other body the route cannot read.
  }
  throw invalid("The request could not be read.");
}

export function frame(event: string, data: unknown, seq: number | null = null): string {
  const numbered = seq === null ? "" : `id: ${seq}\n`;
  return `${numbered}event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

export function openStream(response: ServerResponse): void {
  response.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-store",
    "X-Accel-Buffering": "no",
  });
  response.flushHeaders();
}

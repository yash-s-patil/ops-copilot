import type { NextRequest } from "next/server";
import { allowed, FORWARDED_HEADERS, sameOrigin } from "@/server/bff";
import { bodyBytesMax } from "@/server/limits";
import { settings } from "@/server/settings";
import { accessToken } from "@/server/token";

const JSON_TYPE = "application/json";

function refusal(status: number, code: string, message: string): Response {
  return Response.json({ code, message, retryable: false, details: {} }, { status });
}

const NOT_FOUND = () => refusal(404, "NOT_FOUND", "There is nothing at this address.");
const SIGNED_OUT = () => refusal(401, "SIGNED_OUT", "Sign in again to continue.");
const TOO_LARGE = () => refusal(413, "VALIDATION_FAILED", "The request is too large.");
const UNAVAILABLE = () =>
  refusal(503, "BACKEND_UNAVAILABLE", "Meridian's systems are not answering right now.");

/** What was sent, or null when it is more than `limit` bytes. Counted as it is read. */
async function within(body: ReadableStream<Uint8Array>, limit: number): Promise<Buffer | null> {
  const held: Uint8Array[] = [];
  let size = 0;
  const reader = body.getReader();
  for (let part = await reader.read(); !part.done; part = await reader.read()) {
    size += part.value.byteLength;
    if (size > limit) return null;
    held.push(part.value);
  }
  return Buffer.concat(held);
}

async function bodyOf(request: NextRequest): Promise<string | null | Response> {
  if (request.method !== "POST") return null;
  const limit = bodyBytesMax();
  const declared = Number(request.headers.get("content-length") ?? "0");
  if (!Number.isFinite(declared) || declared > limit) return TOO_LARGE();
  if (request.body === null) return null;
  // A body sent in chunks declares no length, so the count is taken from what arrives.
  const sent = await within(request.body, limit);
  if (sent === null) return TOO_LARGE();
  return sent.byteLength === 0 ? null : sent.toString("utf8");
}

function headersFor(request: NextRequest, token: string): Headers {
  const headers = new Headers({ Authorization: `Bearer ${token}` });
  for (const name of FORWARDED_HEADERS) {
    const value = request.headers.get(name);
    if (value !== null) headers.set(name, value);
  }
  return headers;
}

function passedOn(upstream: Response, stream: boolean): Response {
  if (stream && upstream.ok) {
    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
      },
    });
  }
  const headers = new Headers({ "Content-Type": JSON_TYPE, "Cache-Control": "no-store" });
  const retryAfter = upstream.headers.get("retry-after");
  if (retryAfter !== null) headers.set("Retry-After", retryAfter);
  return new Response(upstream.body, { status: upstream.status, headers });
}

async function forward(request: NextRequest, context: RouteContext<"/api/bff/[...path]">) {
  const { path } = await context.params;
  const route = allowed(request.method, path, request.nextUrl.searchParams);
  if (route === null) return NOT_FOUND();
  if (
    request.method === "POST" &&
    !sameOrigin(request.headers.get("origin"), request.headers.get("host"))
  ) {
    return refusal(403, "PERMISSION_DENIED", "This request did not come from this application.");
  }
  const token = await accessToken();
  if (token === null) return SIGNED_OUT();
  const body = await bodyOf(request);
  if (body instanceof Response) return body;
  try {
    const upstream = await fetch(new URL(route.upstream, settings().AGENT_URL), {
      method: request.method,
      headers: headersFor(request, token),
      body,
      cache: "no-store",
      signal: request.signal,
    });
    return passedOn(upstream, route.stream);
  } catch {
    // The agent could not be reached. Its address and the reason stay on the server.
    return UNAVAILABLE();
  }
}

export const GET = forward;
export const POST = forward;

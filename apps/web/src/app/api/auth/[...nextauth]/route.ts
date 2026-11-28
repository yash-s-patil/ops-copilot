import type { NextRequest } from "next/server";
import { handlers } from "@/auth";

const SESSION = "/api/auth/session";

/**
 * The session endpoint is not served. Nothing in this application asks the browser who is
 * signed in: pages are given that by the server.
 */
function served(handler: (request: NextRequest) => Promise<Response>) {
  return (request: NextRequest): Response | Promise<Response> =>
    request.nextUrl.pathname === SESSION ? new Response(null, { status: 404 }) : handler(request);
}

export const GET = served(handlers.GET);
export const POST = served(handlers.POST);

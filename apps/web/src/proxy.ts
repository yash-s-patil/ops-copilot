import { NextResponse } from "next/server";
import { auth } from "@/auth";
import { withRenewedSession } from "@/server/session-cookie";

const SIGN_IN = "/sign-in";
// How Next carries the request headers a proxy changed on to the page or route behind it.
const OVERRIDDEN = "x-middleware-override-headers";
const OVERRIDE_PREFIX = "x-middleware-request-";
const BFF = "/api/bff";

function csp(nonce: string): string {
  const dev = process.env.NODE_ENV === "development";
  // React needs eval in development for its error overlay, and the dev server injects
  // styles without a nonce. Neither applies to a production build.
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${dev ? " 'unsafe-eval'" : ""}`,
    `style-src 'self' ${dev ? "'unsafe-inline'" : `'nonce-${nonce}'`}`,
    // The component library positions menus and tooltips with style attributes.
    "style-src-attr 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");
}

/**
 * Runs before every page and every call to the agent. It renews the session, sends anyone
 * who is not signed in to sign in, and gives each page its own nonce for scripts.
 */
const guarded = auth((request) => {
  const { pathname } = request.nextUrl;
  const signedIn = request.auth !== null && request.auth.error === undefined;
  if (pathname.startsWith(BFF)) {
    if (signedIn) return NextResponse.next();
    return NextResponse.json(
      { code: "SIGNED_OUT", message: "Sign in again to continue.", retryable: false, details: {} },
      { status: 401 },
    );
  }
  if (!signedIn && pathname !== SIGN_IN) {
    return NextResponse.redirect(new URL(SIGN_IN, request.nextUrl.origin));
  }
  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const policy = csp(nonce);
  const headers = new Headers(request.headers);
  headers.set("x-nonce", nonce);
  headers.set("Content-Security-Policy", policy);
  const response = NextResponse.next({ request: { headers } });
  response.headers.set("Content-Security-Policy", policy);
  return response;
});

type Guarded = typeof guarded;

/**
 * The sign-in settings are read on first use, so the wrapped handler arrives as a
 * promise at run time although its type says otherwise.
 */
export async function proxy(...args: Parameters<Guarded>): Promise<Response> {
  const run = await Promise.resolve(guarded);
  const response = (await run(...args)) ?? NextResponse.next();
  const renewed = response.headers.getSetCookie();
  if (renewed.length > 0 && response.headers.has(OVERRIDDEN)) {
    const [request] = args;
    const cookie = withRenewedSession(request.headers.get("cookie") ?? "", renewed);
    response.headers.set(`${OVERRIDE_PREFIX}cookie`, cookie);
  }
  return response;
}

export const config = {
  matcher: ["/((?!api/auth|healthz|_next/|favicon.ico).*)"],
};

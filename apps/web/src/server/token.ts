import "server-only";
import { headers } from "next/headers";
import { getToken } from "next-auth/jwt";
import { settings } from "@/server/settings";

const MS_PER_S = 1000;
const SECURE_COOKIE = "__Secure-authjs.session-token";

/**
 * The signed-in person's token for the agent, or null. Read from the session cookie and
 * from nothing else: a request's own Authorization header is never taken as a session.
 */
export async function accessToken(): Promise<string | null> {
  // The header as the browser sent it: rebuilding it from decoded values would let a
  // cookie's value pass for a second cookie.
  const cookie = (await headers()).get("cookie") ?? "";
  const token = await getToken({
    req: { headers: { cookie } },
    secret: settings().AUTH_SECRET,
    secureCookie: cookie.split(";").some((part) => part.trim().startsWith(SECURE_COOKIE)),
  });
  if (token === null || token.error !== undefined || typeof token.accessToken !== "string") {
    return null;
  }
  if (typeof token.expiresAt !== "number" || token.expiresAt * MS_PER_S <= Date.now()) return null;
  return token.accessToken;
}

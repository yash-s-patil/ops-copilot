import { encode, type JWT } from "next-auth/jwt";

export type Cookie = { name: string; value: string };

export const SESSION_COOKIE = "authjs.session-token";
export const SECURE_SESSION_COOKIE = `__Secure-${SESSION_COOKIE}`;

/** The session cookie as sign-in writes it: the claims, encrypted with the session secret. */
export async function sessionCookie(
  secret: string,
  claims: JWT,
  name: string = SESSION_COOKIE,
): Promise<Cookie> {
  return { name, value: await encode({ token: claims, secret, salt: name }) };
}

export function inAnHour(): number {
  return Math.floor(Date.now() / 1000) + 3600;
}

import "server-only";
import { redirect } from "next/navigation";
import { cache } from "react";
import { ADMIN_ROLE } from "@/lib/roles";
import { me, type Me } from "@/lib/schemas";
import { settings } from "@/server/settings";
import { accessToken } from "@/server/token";

const SIGN_IN = "/sign-in";

const HOME = { engineer: "/inbox", admin: "/admin/live" } as const;

/**
 * Who is signed in, as the agent and the MCP server say. Sends anyone else to sign in.
 * Asked once for each page, however many layouts need it.
 */
export const currentUser = cache(async function currentUser(): Promise<Me> {
  const token = await accessToken();
  if (token === null) redirect(SIGN_IN);
  const response = await fetch(new URL("/api/me", settings().AGENT_URL), {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  if (response.status === 401) redirect(SIGN_IN);
  if (!response.ok) throw new Error(`The agent answered ${response.status} to /api/me`);
  return me.parse(await response.json());
});

export function homeOf(person: Me): string {
  return person.roles.includes(ADMIN_ROLE) ? HOME.admin : HOME.engineer;
}

/**
 * Keeps each kind of person on the screens made for them. This is a courtesy: what anyone
 * may read is decided by the servers behind, which refuse the rest.
 */
export async function onlyFor(kind: keyof typeof HOME): Promise<void> {
  const home = homeOf(await currentUser());
  if (home !== HOME[kind]) redirect(home);
}

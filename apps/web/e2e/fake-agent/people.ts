/** The five people of the demonstration, as the fake agent knows them. */
import type { Me } from "@/lib/schemas";

export const USER_KEYS = ["priya", "rohan", "arjun", "kavita", "sam"] as const;
export type UserKey = (typeof USER_KEYS)[number];

export type Person = Me & { key: UserKey };

const L1 = [
  "customers.read",
  "orders.read",
  "tickets.read",
  "runbooks.read",
  "policies.read",
  "tickets.write",
];
const L2 = [...L1, "pii.read", "refunds.request"];
const SUPERVISOR = [...L2, "refunds.approve"];
const ADMIN = ["admin.read", "chaos.write"];

export const ROLE = {
  l1: "Support.L1",
  l2: "Support.L2",
  supervisor: "Support.Supervisor",
  admin: "Platform.Admin",
} as const;

const person = (
  key: UserKey,
  name: string,
  role: string,
  region: string | null,
  permissions: string[],
): Person => ({ key, name, roles: [role], region, permissions, demo_tools: true });

export const PEOPLE: Record<UserKey, Person> = {
  priya: person("priya", "Priya Nair", ROLE.l1, "west", L1),
  rohan: person("rohan", "Rohan Iyer", ROLE.l1, "south", L1),
  arjun: person("arjun", "Arjun Mehta", ROLE.l2, "west", L2),
  kavita: person("kavita", "Kavita Rao", ROLE.supervisor, "*", SUPERVISOR),
  sam: person("sam", "Sam Admin", ROLE.admin, null, ADMIN),
};

const TOKEN_PREFIX = "demo-user:";
const BEARER = "bearer ";

export function isUserKey(value: unknown): value is UserKey {
  return USER_KEYS.some((key) => key === value);
}

/** Not a credential: the token only names the demo user, which is all the fake needs. */
export function tokenFor(key: UserKey): string {
  return Buffer.from(`${TOKEN_PREFIX}${key}`).toString("base64url");
}

export function personOf(authorization: string | undefined): Person | null {
  const header = authorization ?? "";
  if (!header.toLowerCase().startsWith(BEARER)) return null;
  const named = Buffer.from(header.slice(BEARER.length), "base64url").toString("utf8");
  const key = named.startsWith(TOKEN_PREFIX) ? named.slice(TOKEN_PREFIX.length) : "";
  return isUserKey(key) ? PEOPLE[key] : null;
}

export function can(who: Person, permission: string): boolean {
  return who.permissions.includes(permission);
}

export function has(who: Person, role: string): boolean {
  return who.roles.includes(role);
}

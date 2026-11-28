/** The five people of the demonstration. They exist only where sign-in is the local stand-in. */
export const DEMO_USERS = [
  { id: "priya", name: "Priya Nair", role: "L1 · west region" },
  { id: "rohan", name: "Rohan Iyer", role: "L1 · south region" },
  { id: "arjun", name: "Arjun Mehta", role: "L2 · west region" },
  { id: "kavita", name: "Kavita Rao", role: "Supervisor · all regions" },
  { id: "sam", name: "Sam Admin", role: "Admin" },
] as const;

export function isDemoUser(id: unknown): id is (typeof DEMO_USERS)[number]["id"] {
  return DEMO_USERS.some((user) => user.id === id);
}

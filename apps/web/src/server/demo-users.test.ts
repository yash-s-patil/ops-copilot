import { describe, expect, it } from "vitest";
import { DEMO_USERS, isDemoUser } from "@/server/demo-users";

describe("the people of the demonstration", () => {
  it("are five, each with an id of their own", () => {
    expect(DEMO_USERS).toHaveLength(5);
    expect(new Set(DEMO_USERS.map((user) => user.id)).size).toBe(5);
  });

  it("include an L1 and an L2 of the same region, a supervisor and an admin", () => {
    const roles = Object.fromEntries(DEMO_USERS.map((user) => [user.name, user.role]));
    expect(roles).toMatchObject({
      "Priya Nair": "L1 · west region",
      "Arjun Mehta": "L2 · west region",
      "Kavita Rao": "Supervisor · all regions",
      "Sam Admin": "Admin",
    });
  });

  it.each(DEMO_USERS.map((user) => user.id))("accepts %s as one of them", (id) => {
    expect(isDemoUser(id)).toBe(true);
  });

  it.each([
    ["an unknown id", "mallory"],
    ["a display name", "Priya Nair"],
    ["an id in capitals", "PRIYA"],
    ["an id with a space", "priya "],
    ["nothing", ""],
    ["null", null],
    ["undefined", undefined],
    ["a number", 0],
    ["a list holding an id", ["priya"]],
    ["an object", { id: "priya" }],
  ])("refuses %s", (_what, id) => {
    expect(isDemoUser(id)).toBe(false);
  });
});

import { describe, expect, it } from "vitest";
import { withRenewedSession } from "@/server/session-cookie";

const OLD = "old-session-value";
const NEW = "new-session-value";

describe("the cookie a renewed session leaves for the rest of the request", () => {
  it("replaces the session's value and keeps every other cookie", () => {
    const cookie = withRenewedSession(`theme=dark; authjs.session-token=${OLD}; other=1`, [
      `authjs.session-token=${NEW}; Path=/; HttpOnly; SameSite=Lax`,
    ]);

    expect(cookie).toBe(`theme=dark; authjs.session-token=${NEW}; other=1`);
  });

  it("handles the secure name and a session sent in numbered pieces", () => {
    const cookie = withRenewedSession(
      `__Secure-authjs.session-token.0=${OLD}; __Secure-authjs.session-token.1=${OLD}`,
      [
        `__Secure-authjs.session-token.0=${NEW}; Path=/; Secure; HttpOnly`,
        "__Secure-authjs.session-token.1=; Path=/; Max-Age=0",
      ],
    );

    expect(cookie).toBe(`__Secure-authjs.session-token.0=${NEW}`);
  });

  it("drops the session when it is cleared, and leaves the header as it was otherwise", () => {
    expect(
      withRenewedSession(`authjs.session-token=${OLD}; theme=light`, [
        "authjs.session-token=; Path=/; Max-Age=0",
      ]),
    ).toBe("theme=light");
    expect(withRenewedSession(`theme=light`, ["theme=dark; Path=/"])).toBe("theme=light");
    expect(withRenewedSession("", [])).toBe("");
  });

  it("adds a session the request did not carry", () => {
    expect(withRenewedSession("theme=light", [`authjs.session-token=${NEW}; Path=/`])).toBe(
      `theme=light; authjs.session-token=${NEW}`,
    );
  });

  it("ignores a cookie line without a name", () => {
    expect(withRenewedSession("a=1", ["=oops; Path=/", "nonsense"])).toBe("a=1");
  });
});

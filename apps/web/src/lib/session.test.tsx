// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Me } from "@/lib/schemas";
import { regionLabel, roleLabel, SessionProvider, useSession } from "@/lib/session";
import { ARJUN, KAVITA, LIMITS, PRIYA, withDemoTools } from "@/test/fixtures";
import { renderAs, switchTechnicalOn } from "@/test/render";

const withRoles = (...roles: string[]): Me => ({ ...PRIYA, roles });
const inRegion = (region: string | null): Me => ({ ...PRIYA, region });

describe("roleLabel", () => {
  it.each([
    [["Support.L1"], "L1"],
    [["Support.L2"], "L2"],
    [["Support.Supervisor"], "Supervisor"],
    [["Platform.Admin"], "Admin"],
  ])("says %j as %s", (roles, label) => {
    expect(roleLabel(withRoles(...roles))).toBe(label);
  });

  it("is the highest role a person holds, in whatever order they are listed", () => {
    expect(roleLabel(withRoles("Support.L1", "Support.Supervisor", "Support.L2"))).toBe(
      "Supervisor",
    );
    expect(roleLabel(withRoles("Support.L2", "Platform.Admin"))).toBe("Admin");
    expect(roleLabel(KAVITA)).toBe("Supervisor");
  });

  it("says No role for someone with none, or with only roles it does not know", () => {
    expect(roleLabel(withRoles())).toBe("No role");
    expect(roleLabel(withRoles("Finance.Clerk", "support.l1"))).toBe("No role");
  });
});

describe("regionLabel", () => {
  it("names the region", () => {
    expect(regionLabel(inRegion("west"))).toBe("west region");
  });

  it("says all regions for someone not bound to one", () => {
    expect(regionLabel(inRegion("*"))).toBe("all regions");
  });

  it("is null for someone with no region", () => {
    expect(regionLabel(inRegion(null))).toBeNull();
  });
});

function Shows() {
  const { me, limits, technical, can, signInEnded } = useSession();
  return (
    <div>
      <p>{me.name}</p>
      <p>{`refunds.request: ${can("refunds.request")}`}</p>
      <p>{`tickets.write: ${can("tickets.write")}`}</p>
      <p>{`nothing: ${can("")}`}</p>
      <p>{`technical: ${technical}`}</p>
      <p>{`reason: ${limits.reasonChars}`}</p>
      <button type="button" onClick={signInEnded}>
        end
      </button>
    </div>
  );
}

describe("the session", () => {
  it("says who is signed in and gives the limits it was made with", () => {
    renderAs(<Shows />, ARJUN);
    expect(screen.getByText(ARJUN.name)).toBeTruthy();
    expect(screen.getByText(`reason: ${LIMITS.reasonChars}`)).toBeTruthy();
  });

  it("allows exactly the permissions the server listed", () => {
    renderAs(<Shows />, PRIYA);
    expect(screen.getByText("tickets.write: true")).toBeTruthy();
    expect(screen.getByText("refunds.request: false")).toBeTruthy();
    expect(screen.getByText("nothing: false")).toBeTruthy();
  });

  it("allows nothing to someone with no permissions", () => {
    renderAs(<Shows />, { ...PRIYA, permissions: [] });
    expect(screen.getByText("tickets.write: false")).toBeTruthy();
  });

  it("starts with technical details off, and turns them on where the demonstration offers them", async () => {
    const { user } = renderAs(<Shows />, withDemoTools(PRIYA));
    expect(screen.getByText("technical: false")).toBeTruthy();
    await switchTechnicalOn(user);
    expect(screen.getByText("technical: true")).toBeTruthy();
  });

  it("keeps technical details off where they are not offered, even if switched on", async () => {
    const { user } = renderAs(<Shows />, PRIYA);
    await switchTechnicalOn(user);
    expect(screen.getByText("technical: false")).toBeTruthy();
  });

  it("tells whoever watches that the sign-in ended", () => {
    const onSignInEnded = vi.fn();
    render(
      <SessionProvider me={PRIYA} limits={LIMITS} onSignInEnded={onSignInEnded}>
        <Shows />
      </SessionProvider>,
    );
    screen.getByRole("button", { name: "end" }).click();
    expect(onSignInEnded).toHaveBeenCalledTimes(1);
  });

  it("does nothing about a sign-in that ended when nobody watches", async () => {
    const { user } = renderAs(<Shows />);
    await user.click(screen.getByRole("button", { name: "end" }));
    expect(screen.getByText(PRIYA.name)).toBeTruthy();
  });

  it("refuses to be used outside its provider", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    expect(() => render(<Shows />)).toThrow("useSession is used outside SessionProvider");
  });
});

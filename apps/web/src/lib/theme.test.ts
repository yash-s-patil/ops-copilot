// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { setTheme, THEME_COOKIE, themeFrom, THEMES } from "@/lib/theme";

describe("themeFrom", () => {
  it.each(THEMES)("keeps %s", (theme) => {
    expect(themeFrom(theme)).toBe(theme);
  });

  it.each([undefined, "", "Dark", "sepia", "dark; Path=/", "constructor"])(
    "falls back to the system's choice for %j",
    (value) => {
      expect(themeFrom(value)).toBe("system");
    },
  );
});

describe("setTheme", () => {
  let written: string[];

  beforeEach(() => {
    written = [];
    delete document.documentElement.dataset["theme"];
    vi.spyOn(document, "cookie", "set").mockImplementation((value) => {
      written.push(value);
    });
  });

  it.each(["light", "dark"] as const)("marks the page as %s and remembers it", (theme) => {
    setTheme(theme);
    expect(document.documentElement.dataset["theme"]).toBe(theme);
    expect(written).toEqual([`${THEME_COOKIE}=${theme}; Path=/; Max-Age=31536000; SameSite=Lax`]);
  });

  it("removes the mark for the system's choice, and remembers that too", () => {
    setTheme("dark");
    setTheme("system");
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
    expect(written.at(-1)).toContain(`${THEME_COOKIE}=system;`);
  });

  it("keeps the choice for the whole site for a year, and off requests from other sites", () => {
    setTheme("light");
    expect(written[0]).toMatch(/Path=\/; Max-Age=31536000; SameSite=Lax$/);
  });
});

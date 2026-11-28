export const THEMES = ["system", "light", "dark"] as const;
export type Theme = (typeof THEMES)[number];
export const THEME_COOKIE = "theme";
const YEAR_S = 31_536_000;

export function themeFrom(value: string | undefined): Theme {
  return THEMES.find((theme) => theme === value) ?? "system";
}

/** The choice is kept in a cookie so that the server draws the page in it from the first byte. */
export function setTheme(theme: Theme): void {
  document.cookie = `${THEME_COOKIE}=${theme}; Path=/; Max-Age=${YEAR_S}; SameSite=Lax`;
  if (theme === "system") {
    delete document.documentElement.dataset["theme"];
  } else {
    document.documentElement.dataset["theme"] = theme;
  }
}

import type { ThemeMode } from "./types";

export const THEME_STORAGE_KEY = "constella.theme";

export function readThemeMode(): ThemeMode {
  const saved = window.localStorage.getItem(THEME_STORAGE_KEY);
  return saved === "light" || saved === "dark" || saved === "system" ? saved : "system";
}

export function applyDocumentTheme(themeMode: ThemeMode, prefersDark: boolean) {
  const resolved = themeMode === "system" ? (prefersDark ? "dark" : "light") : themeMode;
  document.documentElement.dataset.theme = themeMode;
  document.documentElement.dataset.resolvedTheme = resolved;
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", resolved === "dark" ? "#0f1113" : "#f7f7f4");
  window.localStorage.setItem(THEME_STORAGE_KEY, themeMode);
}

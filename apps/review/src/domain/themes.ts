export const THEMES = [
  { id: "forest", label: "森林绿", swatch: "#1d5a41" },
  { id: "ocean", label: "深海蓝", swatch: "#225f9f" },
  { id: "amber", label: "暖橙", swatch: "#a95519" },
  { id: "violet", label: "紫罗兰", swatch: "#7449a8" },
  { id: "slate", label: "石墨青", swatch: "#2e6570" },
  { id: "dark", label: "暗黑", swatch: "#1d2530" },
] as const;

export type ThemeId = (typeof THEMES)[number]["id"];

const DEFAULT_THEME: ThemeId = "forest";

function isThemeId(value: string | null): value is ThemeId {
  return THEMES.some((theme) => theme.id === value);
}

function storageKey(userId: string): string {
  return `amazon-selection-theme:${userId}`;
}

export function readSavedTheme(userId: string): ThemeId {
  if (!userId) return DEFAULT_THEME;
  try {
    const value = window.localStorage.getItem(storageKey(userId));
    return isThemeId(value) ? value : DEFAULT_THEME;
  } catch {
    return DEFAULT_THEME;
  }
}

export function saveTheme(userId: string, theme: ThemeId): void {
  if (!userId) return;
  try {
    window.localStorage.setItem(storageKey(userId), theme);
  } catch {
    // 飞书内嵌页可能禁用本地存储；本次页面仍可切换主题。
  }
}

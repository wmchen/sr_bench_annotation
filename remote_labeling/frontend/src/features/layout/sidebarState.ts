export type Side = "left" | "right";
export interface SidebarPreference { width: number | null; hidden: boolean }
export interface LayoutPreferences {
  version: 1;
  left: SidebarPreference;
  right: SidebarPreference;
}

export const STORAGE_KEY = "realisr.sidebar-layout.v1";
export const RAIL = 16;
export const HANDLE = 6;
export const EDITOR_MIN = 400;
export const limits = { left: { min: 170, max: 420 }, right: { min: 235, max: 480 } };

/** Keep preferences independent of transient window-size constraints. */
export function defaultPreferences(): LayoutPreferences {
  return { version: 1, left: { width: null, hidden: false }, right: { width: null, hidden: false } };
}

export function clampWidth(side: Side, width: number, maximum = limits[side].max): number {
  return Math.max(limits[side].min, Math.min(maximum, width));
}

/** Invalid or unavailable storage must never prevent opening the workspace. */
export function readPreferences(): LayoutPreferences {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null");
    if (value?.version !== 1) return defaultPreferences();
    const result = defaultPreferences();
    for (const side of ["left", "right"] as const) {
      const pref = value[side];
      if (typeof pref?.hidden !== "boolean" ||
          !(pref.width === null || (typeof pref.width === "number" && Number.isFinite(pref.width)))) {
        return defaultPreferences();
      }
      result[side] = { hidden: pref.hidden, width: pref.width === null ? null : clampWidth(side, pref.width) };
    }
    return result;
  } catch { return defaultPreferences(); }
}

export function writePreferences(preferences: LayoutPreferences): void {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(preferences)); } catch { /* Session-only fallback. */ }
}

/** Shrink pinned panels proportionally while retaining the requested widths. */
export function fitWidths(preferences: LayoutPreferences, container: number): Record<Side, number> {
  const compact = container <= 1250;
  const widths = {
    left: clampWidth("left", preferences.left.width ?? (compact ? 170 : 196)),
    right: clampWidth("right", preferences.right.width ?? (compact ? 235 : 270)),
  };
  const sides = (["left", "right"] as const).filter(side => !preferences[side].hidden);
  const available = container - EDITOR_MIN - sides.length * HANDLE - (2 - sides.length) * RAIL;
  const excess = Math.max(0, sides.reduce((sum, side) => sum + widths[side], 0) - available);
  const reducible = sides.reduce((sum, side) => sum + widths[side] - limits[side].min, 0);
  if (excess && reducible) {
    for (const side of sides) {
      widths[side] -= Math.min(1, excess / reducible) * (widths[side] - limits[side].min);
    }
  }
  return widths;
}

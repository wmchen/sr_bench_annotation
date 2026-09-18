export type CrosshairStyle = "solid" | "dashed" | "dotted";
export interface DisplayPreferences {
  version: 1;
  regionWidth: number;
  crosshairWidth: number;
  crosshairStyle: CrosshairStyle;
  crosshairColor: string;
}

export const DISPLAY_STORAGE_KEY = "realisr.drawing-display.v1";
export const widthLimits = {
  regionWidth: { min: 0.5, max: 6, step: 0.5 },
  crosshairWidth: { min: 0.5, max: 4, step: 0.5 },
};

/** Default display preferences use CSS pixels, independent of image zoom. */
export function defaultDisplayPreferences(): DisplayPreferences {
  return { version: 1, regionWidth: 1.5, crosshairWidth: 1,
    crosshairStyle: "dashed", crosshairColor: "#38d9a9" };
}

/** Ignore invalid stored fields without preventing the workspace from opening. */
export function readDisplayPreferences(): DisplayPreferences {
  const result = defaultDisplayPreferences();
  try {
    const value = JSON.parse(localStorage.getItem(DISPLAY_STORAGE_KEY) ?? "null");
    if (value?.version !== 1) return result;
    for (const key of ["regionWidth", "crosshairWidth"] as const) {
      const width = value[key], { min, max, step } = widthLimits[key];
      if (typeof width === "number" && Number.isFinite(width) &&
          width >= min && width <= max && width % step === 0) result[key] = width;
    }
    if (["solid", "dashed", "dotted"].includes(value.crosshairStyle)) result.crosshairStyle = value.crosshairStyle;
    if (typeof value.crosshairColor === "string" && /^#[0-9a-f]{6}$/i.test(value.crosshairColor)) {
      result.crosshairColor = value.crosshairColor.toLowerCase();
    }
  } catch { /* Defaults also work when browser storage is unavailable. */ }
  return result;
}

/** Keep display preferences local; never write them into annotations. */
export function writeDisplayPreferences(value: DisplayPreferences): void {
  try { localStorage.setItem(DISPLAY_STORAGE_KEY, JSON.stringify(value)); } catch { /* Session-only fallback. */ }
}

/** Preserve the selected-region emphasis at every configured width. */
export function regionStrokeWidth(value: DisplayPreferences, selected: boolean): number {
  return value.regionWidth + (selected ? 1 : 0);
}

/** Share the same CSS-pixel dash pattern between the canvas and its preview. */
export function crosshairDash(style: CrosshairStyle, width: number): number[] {
  if (style === "solid") return [];
  return style === "dashed" ? [6, 4] : [width, Math.max(3, width * 2)];
}

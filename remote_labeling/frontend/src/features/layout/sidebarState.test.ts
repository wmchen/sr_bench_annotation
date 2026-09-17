import { afterEach, describe, expect, it, vi } from "vitest";
import { defaultPreferences, fitWidths, readPreferences, STORAGE_KEY, writePreferences } from "./sidebarState";

afterEach(() => vi.unstubAllGlobals());

describe("sidebar layout preferences", () => {
  it("preserves responsive defaults until a width is explicitly chosen", () => {
    const preferences = defaultPreferences();
    expect(fitWidths(preferences, 1600)).toEqual({ left: 196, right: 270 });
    expect(fitWidths(preferences, 1100)).toEqual({ left: 170, right: 235 });
    preferences.left.width = 300;
    expect(fitWidths(preferences, 1100)).toEqual({ left: 300, right: 235 });
  });

  it("shrinks only pinned panels proportionally and restores preferred widths", () => {
    const preferences = defaultPreferences();
    preferences.left.width = 420;
    preferences.right.width = 480;
    const narrow = fitWidths(preferences, 1050);
    expect(narrow.left + narrow.right + 12 + 400).toBeCloseTo(1050);
    expect((420 - narrow.left) / 250).toBeCloseTo((480 - narrow.right) / 245);
    expect(fitWidths(preferences, 1600)).toEqual({ left: 420, right: 480 });
    preferences.left.hidden = true;
    expect(fitWidths(preferences, 1050)).toEqual({ left: 420, right: 480 });
    expect(preferences.right.width).toBe(480);
  });

  it("round-trips independent widths and hidden flags, clamping out-of-range values", () => {
    const data = new Map<string, string>();
    vi.stubGlobal("localStorage", { getItem: (key: string) => data.get(key), setItem: (key: string, value: string) => data.set(key, value) });
    const preferences = defaultPreferences();
    preferences.left = { width: 310, hidden: true };
    writePreferences(preferences);
    expect(readPreferences()).toEqual(preferences);
    data.set(STORAGE_KEY, JSON.stringify({ ...preferences, right: { width: 10000, hidden: false } }));
    expect(readPreferences().right.width).toBe(480);
  });

  it.each(["{", "null", '{"version":2}', '{"version":1,"left":{"width":"300","hidden":true}}'])
    ("falls back safely for invalid saved configuration %s", value => {
      vi.stubGlobal("localStorage", { getItem: () => value });
      expect(readPreferences()).toEqual(defaultPreferences());
    });

  it("continues without persistence when storage access is denied", () => {
    vi.stubGlobal("localStorage", { getItem: () => { throw new Error("denied"); }, setItem: () => { throw new Error("denied"); } });
    expect(readPreferences()).toEqual(defaultPreferences());
    expect(() => writePreferences(defaultPreferences())).not.toThrow();
  });
});

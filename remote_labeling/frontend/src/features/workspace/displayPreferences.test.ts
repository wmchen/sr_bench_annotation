import { afterEach, describe, expect, it, vi } from "vitest";
import { crosshairDash, defaultDisplayPreferences, DISPLAY_STORAGE_KEY, readDisplayPreferences, regionStrokeWidth, writeDisplayPreferences } from "./displayPreferences";

afterEach(() => vi.unstubAllGlobals());

describe("drawing display preferences", () => {
  it("defaults to thin region borders and a green dashed crosshair", () => {
    expect(defaultDisplayPreferences()).toEqual({ version: 1, regionWidth: 1.5,
      crosshairWidth: 1, crosshairStyle: "dashed", crosshairColor: "#38d9a9" });
  });

  it("round-trips personal preferences independently of annotations", () => {
    const data = new Map<string, string>();
    vi.stubGlobal("localStorage", { getItem: (key: string) => data.get(key), setItem: (key: string, value: string) => data.set(key, value) });
    const value = { ...defaultDisplayPreferences(), regionWidth: 6, crosshairWidth: 0.5,
      crosshairStyle: "dotted" as const, crosshairColor: "#ff0000" };
    writeDisplayPreferences(value);
    expect(readDisplayPreferences()).toEqual(value);
    expect([...data.keys()]).toEqual([DISPLAY_STORAGE_KEY]);
  });

  it.each(["{", "null", '{"version":2}', '[]'])("ignores invalid storage %s", value => {
    vi.stubGlobal("localStorage", { getItem: () => value });
    expect(readDisplayPreferences()).toEqual(defaultDisplayPreferences());
  });

  it.each([-1, 0, 0.7, 100, "2", null])("rejects invalid widths %s while keeping valid fields", width => {
    vi.stubGlobal("localStorage", { getItem: () => JSON.stringify({ ...defaultDisplayPreferences(),
      regionWidth: width, crosshairWidth: width, crosshairStyle: "solid", crosshairColor: "#ABCDEF" }) });
    expect(readDisplayPreferences()).toEqual({ ...defaultDisplayPreferences(), crosshairStyle: "solid", crosshairColor: "#abcdef" });
  });

  it("rejects invalid colors and styles", () => {
    vi.stubGlobal("localStorage", { getItem: () => JSON.stringify({ ...defaultDisplayPreferences(),
      regionWidth: 3, crosshairColor: "green", crosshairStyle: "unknown" }) });
    expect(readDisplayPreferences()).toEqual({ ...defaultDisplayPreferences(), regionWidth: 3 });
  });

  it("survives unavailable browser storage", () => {
    vi.stubGlobal("localStorage", { getItem: () => { throw new Error("denied"); }, setItem: () => { throw new Error("denied"); } });
    expect(readDisplayPreferences()).toEqual(defaultDisplayPreferences());
    expect(() => writeDisplayPreferences(defaultDisplayPreferences())).not.toThrow();
  });

  it.each([0.5, 1.5, 6])("keeps selection exactly one pixel thicker at %s px", regionWidth => {
    const value = { ...defaultDisplayPreferences(), regionWidth };
    expect(regionStrokeWidth(value, false)).toBe(regionWidth);
    expect(regionStrokeWidth(value, true)).toBe(regionWidth + 1);
  });

  it("distinguishes solid, dashed and dotted patterns including thick dots", () => {
    expect(crosshairDash("solid", 1)).toEqual([]);
    expect(crosshairDash("dashed", 1)).toEqual([6, 4]);
    expect(crosshairDash("dotted", 1)).toEqual([1, 3]);
    expect(crosshairDash("dotted", 4)).toEqual([4, 8]);
  });
});

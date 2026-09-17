import { describe, expect, it } from "vitest";
import { selectRegion, type RegionSelection } from "./selection";

const order = ["a", "b", "c", "d", "e", "f"];
const plain = {ctrlKey:false, metaKey:false, shiftKey:false};
const shift = {...plain, shiftKey:true};
const empty: RegionSelection = {ids:[], anchor:null};

describe("region list selection", () => {
  it.each(["ctrlKey", "metaKey"] as const)("toggles individual items with %s and resets the range anchor", key => {
    const modifier = {...plain, [key]:true};
    let selection = selectRegion(empty, order, "b", plain);
    selection = selectRegion(selection, order, "f", modifier);
    expect(selection.ids).toEqual(["b", "f"]);
    selection = selectRegion(selection, order, "f", modifier);
    expect(selection).toEqual({ids:["b"], anchor:"f"});
    expect(selectRegion(selection, order, "d", shift).ids).toEqual(["d", "e", "f"]);
    expect(selectRegion(selection, order, "c", plain)).toEqual({ids:["c"], anchor:"c"});
  });

  it("extends, shrinks and reverses a range without moving its anchor", () => {
    let selection = selectRegion(empty, order, "c", plain);
    selection = selectRegion(selection, order, "f", shift);
    expect(selection.ids).toEqual(["c", "d", "e", "f"]);
    selection = selectRegion(selection, order, "d", shift);
    expect(selection.ids).toEqual(["c", "d"]);
    selection = selectRegion(selection, order, "a", shift);
    expect(selection).toEqual({ids:["a", "b", "c"], anchor:"c"});
    expect(selectRegion(selection, order, "c", shift).ids).toEqual(["c"]);
  });

  it.each(["ctrlKey", "metaKey"] as const)("adds a range with Shift and %s without duplicates", key => {
    const selection = {ids:["a", "d"], anchor:"d"};
    expect(selectRegion(selection, order, "f", {...shift, [key]:true}).ids).toEqual(["a", "d", "e", "f"]);
    expect(selectRegion(selection, order, "f", shift).ids).toEqual(["d", "e", "f"]);
  });

  it("starts a new range after clearing selection or removing the anchor", () => {
    expect(selectRegion(empty, order, "e", shift)).toEqual({ids:["e"], anchor:"e"});
    const stale = {ids:["removed", "b"], anchor:"removed"};
    expect(selectRegion(stale, order, "e", shift)).toEqual({ids:["e"], anchor:"e"});
    expect(selectRegion(stale, order, "e", {...shift, ctrlKey:true}).ids).toEqual(["b", "e"]);
  });

  it("uses current list order after reordering", () => {
    const selection = {ids:["b"], anchor:"b"};
    expect(selectRegion(selection, ["b", "e", "d", "a", "c", "f"], "a", shift).ids).toEqual(["b", "e", "d", "a"]);
  });
});

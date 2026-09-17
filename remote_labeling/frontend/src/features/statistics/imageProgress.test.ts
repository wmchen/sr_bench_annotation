import { describe, expect, it } from "vitest";
import { type Group, type Region, variants } from "../../api/client";
import { evidence } from "../../state/domain";
import { imageProgress } from "./imageProgress";

const region = (id: string, value: 0 | 1 | 2 | null): Region => ({ region_id: id, recoverable: value,
  label: "text", description: "", shape_type: "rectangle", points: [[1, 1], [10, 10]] });
const empty = (): Group => ({ HR: [], LR2: [], LR3: [], LR4: [] });

describe("current displayed image progress", () => {
  it("counts all evidence values and uses region IDs across variants, ignoring monotonicity", () => {
    const group: Group = { HR: [region("a", 0), region("b", null)], LR2: [region("b", null), region("a", 2)],
      LR3: [region("a", 1), region("b", null)], LR4: [region("a", 2), region("b", null)] };
    const stats = imageProgress(group);
    expect(stats).toMatchObject({ regions: 2, completeRegions: 1, assigned: 4, total: 8 });
    expect(stats.byVariant.HR).toEqual({ assigned: 1, total: 2 });
    group.LR3[0].region_id = "unmatched";
    expect(imageProgress(group).completeRegions).toBe(0);
  });
  it("reflects local evidence changes, deletions and undo without changing source groups", () => {
    const group = Object.fromEntries(variants.map(v => [v, [region("a", v === "HR" ? 0 : null)]])) as Group;
    const original = structuredClone(group);
    const next = evidence(group, "LR2", ["a"], 0);
    expect(imageProgress(group).assigned).toBe(1);
    expect(imageProgress(next).assigned).toBe(2);
    expect(imageProgress(empty())).toMatchObject({ assigned: 0, total: 0, regions: 0, completeRegions: 0 });
    expect(imageProgress(original)).toEqual(imageProgress(group));
    expect(group).toEqual(original);
  });
  it("treats a default text HR assignment as one completed entry, not a completed group", () => {
    const group = Object.fromEntries(variants.map(v => [v, [region("a", v === "HR" ? 0 : null)]])) as Group;
    expect(imageProgress(group)).toMatchObject({ assigned: 1, total: 4, completeRegions: 0 });
    const face = Object.fromEntries(variants.map(v => [v, [region("a", null)]])) as Group;
    expect(imageProgress(face).assigned).toBe(0);
  });
});

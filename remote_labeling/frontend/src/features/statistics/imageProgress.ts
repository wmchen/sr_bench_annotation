import { variants, type Group, type Variant } from "../../api/client";

export interface VariantProgress { assigned: number; total: number }
export interface ImageProgress {
  regions: number;
  completeRegions: number;
  assigned: number;
  total: number;
  byVariant: Record<Variant, VariantProgress>;
}

/** Count the displayed group, including unsaved edits, using stable region IDs. */
export function imageProgress(group: Group): ImageProgress {
  const assignedIds = new Map<Variant, Set<string>>();
  const byVariant = {} as Record<Variant, VariantProgress>;
  for (const variant of variants) {
    const assigned = group[variant].filter(region => [0, 1, 2].includes(region.recoverable as number));
    assignedIds.set(variant, new Set(assigned.map(region => region.region_id)));
    byVariant[variant] = { assigned: assigned.length, total: group[variant].length };
  }
  return {
    regions: group.HR.length,
    completeRegions: group.HR.filter(region => variants.every(v => assignedIds.get(v)!.has(region.region_id))).length,
    assigned: variants.reduce((sum, v) => sum + byVariant[v].assigned, 0),
    total: variants.reduce((sum, v) => sum + byVariant[v].total, 0),
    byVariant,
  };
}

import type { Group, Sample } from "../../api/client";

/** Compare JSON values semantically while retaining region and vertex order. */
export function sameContent(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (left === null || right === null || typeof left !== "object" || typeof right !== "object") return false;
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left) && Array.isArray(right) && left.length === right.length && left.every((value,index)=>sameContent(value,right[index]));
  }
  const a=left as Record<string,unknown>, b=right as Record<string,unknown>;
  const keys=Object.keys(a);
  return keys.length===Object.keys(b).length && keys.every(key=>Object.hasOwn(b,key) && sameContent(a[key],b[key]));
}

/** Local edits participate before the debounced draft request reaches the server. */
export function needsWriteback(sample: Sample, draft: Group): boolean {
  return !sample.source_ready || !sameContent(draft,sample.source_group);
}

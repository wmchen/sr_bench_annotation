export interface RegionSelection {
  ids: string[];
  anchor: string | null;
}

interface SelectionModifiers {
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
}

/** Select a list range from a stable anchor, or toggle one region. */
export function selectRegion(
  selection: RegionSelection,
  order: string[],
  id: string,
  modifiers: SelectionModifiers,
): RegionSelection {
  const toggle = modifiers.ctrlKey || modifiers.metaKey;
  const ids = selection.ids.filter(value => order.includes(value));
  const anchorIndex = selection.anchor === null ? -1 : order.indexOf(selection.anchor);
  const index = order.indexOf(id);
  if (index < 0) return selection;
  if (modifiers.shiftKey && anchorIndex >= 0) {
    const range = order.slice(Math.min(anchorIndex, index), Math.max(anchorIndex, index) + 1);
    return {
      ids: toggle ? [...new Set([...ids, ...range])] : range,
      anchor: selection.anchor,
    };
  }
  return {
    ids: toggle ? ids.includes(id) ? ids.filter(value => value !== id) : [...ids, id] : [id],
    anchor: id,
  };
}

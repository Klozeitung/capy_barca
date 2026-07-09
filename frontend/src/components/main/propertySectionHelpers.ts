/**
 * propertySectionHelpers
 *
 * Pure, side-effect-free helpers extracted from BlockPropertySection so the
 * non-trivial group / view transformations can be unit-tested in isolation.
 *
 * - schemaIdsInGroup       – collect schema IDs belonging to a group
 * - removeGroupFromOrder   – drop a group from the persisted custom-group order
 * - removeGroupFromFolded  – drop a group key from the fold-state map (immutable)
 * - reorderGroups          – move a custom group before a drop target (immutable)
 * - hideSchemaInAllViews   – add a schema ID to every view's hiddenColumns
 */
import type { DatabaseView, PropertySchema } from '@/stores/database'

/**
 * Collect the IDs of all schemas that belong to `groupName`, treating an
 * absent / empty `schema.group` as membership in `defaultGroup`.
 */
export function schemaIdsInGroup(
  schemas: PropertySchema[],
  groupName: string,
  defaultGroup: string,
): string[] {
  return schemas
    .filter((s) => (s.group || defaultGroup) === groupName)
    .map((s) => s.id)
}

/** Return a copy of `order` with `group` removed. */
export function removeGroupFromOrder(order: string[], group: string): string[] {
  return order.filter((g) => g !== group)
}

/**
 * Return a copy of the fold-state map with `group` removed. The original
 * object is never mutated.
 */
export function removeGroupFromFolded(
  folded: Record<string, boolean>,
  group: string,
): Record<string, boolean> {
  const next = { ...folded }
  delete next[group]
  return next
}

/**
 * Move `sourceGroup` to the drop position of `targetGroup` within `order`.
 *
 * The dragged group lands next to the target on the side it travelled from:
 * dragging forward inserts it after the target, dragging backward inserts it
 * before the target. This mirrors the standard drag-reorder behaviour and is
 * equivalent to removing the source, then re-inserting it at the target's
 * original index.
 *
 * The input array is never mutated: a new array is always returned. When either
 * group is absent, or source and target are identical, a copy of the original
 * order is returned unchanged.
 */
export function reorderGroups(
  order: string[],
  sourceGroup: string,
  targetGroup: string,
): string[] {
  if (sourceGroup === targetGroup) return order.slice()

  const fromIdx = order.indexOf(sourceGroup)
  const toIdx = order.indexOf(targetGroup)
  if (fromIdx === -1 || toIdx === -1) return order.slice()

  const next = order.slice()
  next.splice(fromIdx, 1)
  next.splice(toIdx, 0, sourceGroup)
  return next
}

/**
 * Add `schemaId` to the `hiddenColumns` of every view.
 *
 * Returns a new `views` array (copied immutably) together with a `changed`
 * flag indicating whether any view was actually modified. Views that already
 * hide the schema are left untouched, and when nothing changes the original
 * array reference is returned so callers can skip a redundant persist.
 */
export function hideSchemaInAllViews(
  views: DatabaseView[],
  schemaId: string,
): { views: DatabaseView[]; changed: boolean } {
  let changed = false
  const next = views.map((view) => {
    const hidden = view.hiddenColumns ?? []
    if (hidden.includes(schemaId)) return view
    changed = true
    return { ...view, hiddenColumns: [...hidden, schemaId] }
  })
  return { views: changed ? next : views, changed }
}

/**
 * propertySectionHelpers
 *
 * Pure, side-effect-free helpers extracted from BlockPropertySection so the
 * non-trivial group / view transformations can be unit-tested in isolation.
 *
 * - schemaIdsInGroup       – collect schema IDs belonging to a group
 * - removeGroupFromOrder   – drop a group from the persisted custom-group order
 * - removeGroupFromFolded  – drop a group key from the fold-state map (immutable)
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

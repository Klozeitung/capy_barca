/**
 * propertySectionHelpers
 *
 * Pure, side-effect-free helpers extracted from BlockPropertySection so the
 * non-trivial group transformations can be unit-tested in isolation.
 *
 * - schemaIdsInGroup       – collect schema IDs belonging to a group
 * - removeGroupFromOrder   – drop a group from the persisted custom-group order
 * - removeGroupFromFolded  – drop a group key from the fold-state map (immutable)
 * - reorderGroups          – move a custom group before a drop target (immutable)
 * - isPropertyValueEmpty   – whether a stored value counts as empty
 * - isSchemaVisible        – whether a property shows for an entry, given its mode
 * - orderSchemaGroups      – the visible properties, grouped and in display order
 *
 * The last three are shared with Quick Print, which prints an entry's
 * properties in the order and with the visibility the property section shows.
 */
import type { PropertySchema, DatabaseEntry } from '@/stores/database'

/** Group of properties without an explicit group; stored as this name. */
export const DEFAULT_GROUP = 'Standard'

/** Block preference of the database: order of the custom groups. */
export const PREF_GROUP_ORDER = 'property_group_order'

/** Block preference of the database: visibility mode per property. */
export const PREF_VISIBILITY = 'property_sideview_visibility'

/** How a property is shown in the property section. */
export type PropertyVisibility = 'show' | 'hide_empty' | 'hide'

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
 * Whether a stored property value counts as empty: absent, an empty text, no
 * linked entries, no files, an unchecked checkbox or no selected option.
 */
export function isPropertyValueEmpty(val: Record<string, unknown> | null | undefined): boolean {
  if (val === undefined || val === null) return true
  if (typeof val !== 'object') return false
  // Text/number/url/email/phone: { value: '' }
  if ('value' in val && (val.value === '' || val.value === null || val.value === undefined)) return true
  // Relation: { related_ids: [] }
  if ('related_ids' in val && Array.isArray(val.related_ids) && val.related_ids.length === 0) return true
  // File: { files: [] }
  if ('files' in val && Array.isArray(val.files) && val.files.length === 0) return true
  // Checkbox: { checked: false } counts as empty.
  if ('checked' in val && val.checked === false) return true
  // Select: { selected: '' } or { selected: [] }
  if ('selected' in val) {
    if (val.selected === '' || val.selected === null) return true
    if (Array.isArray(val.selected) && val.selected.length === 0) return true
  }
  return false
}

/** Whether *schema* shows for *entry* under the visibility *mode*. */
export function isSchemaVisible(
  schema: PropertySchema,
  mode: PropertyVisibility | undefined,
  entry: DatabaseEntry,
): boolean {
  const effective = mode ?? 'show'
  if (effective === 'hide') return false
  if (effective === 'hide_empty') return !isPropertyValueEmpty(entry.values[schema.id])
  return true
}

/** A group of properties as the property section shows it. */
export interface OrderedSchemaGroup {
  name: string
  isDefault: boolean
  schemas: PropertySchema[]
}

/**
 * Group *schemas* and put them in display order.
 *
 * The default group comes first, then the custom groups in *groupOrder*, then
 * any remaining groups alphabetically. Groups named in *groupOrder* that hold
 * no property anymore are kept, empty, so they can still be shown and filled.
 * Within a group, properties follow their ``position``; those for which
 * *isVisible* is false are left out.
 */
export function orderSchemaGroups(
  schemas: PropertySchema[],
  groupOrder: string[],
  defaultGroup: string,
  isVisible: (schema: PropertySchema) => boolean,
): OrderedSchemaGroup[] {
  const groupMap = new Map<string, PropertySchema[]>()
  for (const s of schemas) {
    const g = s.group || defaultGroup
    if (!groupMap.has(g)) groupMap.set(g, [])
    groupMap.get(g)!.push(s)
  }
  for (const g of groupOrder) {
    if (!groupMap.has(g)) groupMap.set(g, [])
  }
  for (const [g, arr] of groupMap) {
    groupMap.set(g, arr.slice().sort((a, b) => a.position - b.position).filter(isVisible))
  }

  const ordered: OrderedSchemaGroup[] = []
  if (groupMap.has(defaultGroup)) {
    ordered.push({ name: defaultGroup, isDefault: true, schemas: groupMap.get(defaultGroup)! })
    groupMap.delete(defaultGroup)
  }
  for (const g of groupOrder) {
    if (g === defaultGroup || !groupMap.has(g)) continue
    ordered.push({ name: g, isDefault: false, schemas: groupMap.get(g)! })
    groupMap.delete(g)
  }
  for (const g of [...groupMap.keys()].sort()) {
    ordered.push({ name: g, isDefault: false, schemas: groupMap.get(g)! })
  }
  return ordered
}

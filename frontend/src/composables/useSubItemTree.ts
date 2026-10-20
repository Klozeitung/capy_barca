/**
 * useSubItemTree
 *
 * The sub-item hierarchy of a database table: which entry is the parent of
 * which, which rows are folded, and the rows in tree order with their depth.
 *
 * The tree is built from the ``parent_item`` values of the loaded entries, the
 * values the user writes directly. The ``sub_item`` values are a mirror the
 * backend maintains; building from them made entries vanish from the tree
 * whenever the mirror on the parent was stale, for instance right after a
 * ``parent_item`` write and before the change event arrived.
 *
 * Fold state is kept per database in localStorage; every entry starts
 * unfolded.
 *
 * Usage:
 *   const tree = useSubItemTree({ schemas, entries, isGrouped, storageKey })
 *   tree.loadFoldState()            // once, on mount
 *   // template: v-for="{ entry, depth } in tree.flatTreeEntries"
 */
import { computed, ref, type Ref } from 'vue'
import type { DatabaseEntry, PropertySchema } from '@/stores/database'

/** One table row in tree order. */
export interface FlatTreeEntry {
  entry: DatabaseEntry
  depth: number
}

/** Entry id to parent id, from the ``parent_item`` values of *entries*. */
export function parentMapOf(
  entries: readonly DatabaseEntry[],
  parentItemSchemaId: string,
): Map<string, string> {
  const map = new Map<string, string>()
  for (const entry of entries) {
    const parents = (entry.values[parentItemSchemaId]?.related_ids as string[] | undefined) ?? []
    if (parents.length > 0) map.set(entry.id, parents[0])
  }
  return map
}

/** Parent id to child ids, the inverse of *parentMap*. */
export function childrenMapOf(parentMap: ReadonlyMap<string, string>): Map<string, string[]> {
  const map = new Map<string, string[]>()
  for (const [childId, parentId] of parentMap) {
    const existing = map.get(parentId)
    if (existing) existing.push(childId)
    else map.set(parentId, [childId])
  }
  return map
}

/**
 * *entries* in tree order: every root, followed depth-first by its children.
 *
 * Roots are the entries whose parent is not among *entries* (none, or not
 * loaded). Siblings keep the order of *entries*, which is the server's sort.
 * The children of a folded entry are left out. A cycle in broken data is cut
 * where an entry would appear a second time.
 */
export function flattenTree(
  entries: readonly DatabaseEntry[],
  parentMap: ReadonlyMap<string, string>,
  childMap: ReadonlyMap<string, string[]>,
  folded: ReadonlySet<string>,
): FlatTreeEntry[] {
  const entryById = new Map(entries.map(e => [e.id, e]))
  const indexById = new Map(entries.map((e, i) => [e.id, i]))
  const roots = entries.filter(e => {
    const parentId = parentMap.get(e.id)
    return !parentId || !entryById.has(parentId)
  })

  const result: FlatTreeEntry[] = []
  const visited = new Set<string>()

  function walk(entry: DatabaseEntry, depth: number): void {
    if (visited.has(entry.id)) return
    visited.add(entry.id)
    result.push({ entry, depth })
    if (folded.has(entry.id)) return
    const children = (childMap.get(entry.id) ?? [])
      .map(id => entryById.get(id))
      .filter((e): e is DatabaseEntry => e !== undefined)
      .sort((a, b) => indexById.get(a.id)! - indexById.get(b.id)!)
    for (const child of children) walk(child, depth + 1)
  }

  for (const entry of roots) walk(entry, 0)
  return result
}

export function useSubItemTree(options: {
  schemas: Readonly<Ref<PropertySchema[]>>
  /** The loaded entries, in the order the server sorted them. */
  entries: Readonly<Ref<DatabaseEntry[]>>
  /** Grouped tables render flat. */
  isGrouped: Readonly<Ref<boolean>>
  /** localStorage key of the fold state. */
  storageKey: string
}) {
  const { schemas, entries, isGrouped, storageKey } = options

  // ── Schemas ─────────────────────────────────────────────────────────────────

  /** The parent_item schema of this database, if seeded. */
  const parentItemSchema = computed(() =>
    schemas.value.find(s => s.type === 'parent_item') ?? null,
  )
  /** The sub_item schema of this database, if seeded. */
  const subItemSchema = computed(() =>
    schemas.value.find(s => s.type === 'sub_item') ?? null,
  )

  // ── Hierarchy ───────────────────────────────────────────────────────────────

  /** Entry id to parent id; empty until the parent_item schema exists. */
  const subItemParentMap = computed((): Map<string, string> =>
    parentItemSchema.value ? parentMapOf(entries.value, parentItemSchema.value.id) : new Map(),
  )

  /** Entry id to child ids; empty until the sub_item schema exists. */
  const subItemChildrenMap = computed((): Map<string, string[]> =>
    subItemSchema.value ? childrenMapOf(subItemParentMap.value) : new Map(),
  )

  // ── Fold state ──────────────────────────────────────────────────────────────

  const foldedEntries = ref<Set<string>>(new Set())

  function loadFoldState(): void {
    try {
      const raw = localStorage.getItem(storageKey)
      if (raw) foldedEntries.value = new Set(JSON.parse(raw))
    } catch { /* ignore */ }
  }

  function saveFoldState(): void {
    try {
      localStorage.setItem(storageKey, JSON.stringify([...foldedEntries.value]))
    } catch { /* ignore */ }
  }

  function toggleFold(entryId: string): void {
    const next = new Set(foldedEntries.value)
    if (next.has(entryId)) next.delete(entryId)
    else next.add(entryId)
    foldedEntries.value = next
    saveFoldState()
  }

  // ── Rows ────────────────────────────────────────────────────────────────────

  /**
   * The rows of the table: an indented tree while the sub-item pair exists
   * and the table is not grouped, the entries as they are otherwise.
   */
  const flatTreeEntries = computed((): FlatTreeEntry[] => {
    if (isGrouped.value || !subItemSchema.value) {
      return entries.value.map(e => ({ entry: e, depth: 0 }))
    }
    return flattenTree(
      entries.value, subItemParentMap.value, subItemChildrenMap.value, foldedEntries.value,
    )
  })

  return {
    parentItemSchema,
    subItemSchema,
    subItemParentMap,
    subItemChildrenMap,
    foldedEntries,
    loadFoldState,
    toggleFold,
    flatTreeEntries,
  }
}

/**
 * viewSettingsHelpers
 *
 * Pure, side-effect-free helpers extracted from ViewSettingsModal so the
 * non-trivial column ordering / visibility splitting can be unit-tested in
 * isolation.
 *
 * - orderAndSplitColumns – sort property columns alphabetically by name and
 *   split them into a visible group and a hidden group.
 */

/**
 * Minimal shape a column descriptor must expose to be ordered and split.
 * Concrete callers may extend this with further display fields (icon, …).
 */
export interface OrderableColumn {
  /** Stable key: NAME_COL_KEY for the name column, otherwise the schema id. */
  key: string
  /** Display name, used as the alphabetical sort key. */
  name: string
  /** True for the always-visible name column, which has no visibility toggle. */
  isName: boolean
}

export interface SplitColumns<T extends OrderableColumn> {
  /** Visible columns. The name column is always first, the rest alphabetical. */
  visible: T[]
  /** Hidden columns, alphabetical. */
  hidden: T[]
}

/**
 * Sort `columns` alphabetically by display name and split them into a visible
 * and a hidden group.
 *
 * Ordering rules:
 * - The name column is always first within the visible group; it is the
 *   entry's anchor and is never folded into the alphabetical run.
 * - Every other column is sorted alphabetically by `name`, case-insensitively
 *   and with natural numeric ordering (e.g. "Item 2" before "Item 10").
 *
 * Splitting rules:
 * - The name column is always visible (it has no toggle).
 * - Every other column lands in `hidden` when its key is in `hiddenKeys`,
 *   otherwise in `visible`. Each group keeps the alphabetical order.
 *
 * The input array is never mutated.
 */
export function orderAndSplitColumns<T extends OrderableColumn>(
  columns: T[],
  hiddenKeys: Set<string>,
): SplitColumns<T> {
  const nameCol = columns.find((c) => c.isName)
  const rest = columns
    .filter((c) => !c.isName)
    .sort((a, b) =>
      a.name.localeCompare(b.name, undefined, { sensitivity: 'base', numeric: true }),
    )

  const visible: T[] = []
  const hidden: T[] = []

  if (nameCol) visible.push(nameCol)

  for (const col of rest) {
    if (hiddenKeys.has(col.key)) hidden.push(col)
    else visible.push(col)
  }

  return { visible, hidden }
}

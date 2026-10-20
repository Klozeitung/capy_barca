/**
 * useStickyColumns
 *
 * Sticky column header (vertical scroll) and frozen leftmost columns
 * (horizontal scroll) of the table view, as the active view configures them.
 *
 * Frozen left offsets are measured from the rendered header-cell widths
 * (``offsetWidth``, independent of scrolling) rather than ``offsetLeft``, so a
 * re-measure while the table is scrolled horizontally cannot corrupt them.
 * The handle column is pinned (``left: 0``) whenever at least one column is
 * frozen. The offsets follow the header row's size and the window's.
 *
 * Usage:
 *   const sticky = useStickyColumns({ activeView, activeViewId, orderedColumns })
 *   // template: <tr :ref="sticky.theadRowEl"> … :style="sticky.frozenColStyle(key)"
 *   sticky.start()  // once the table is rendered
 *   sticky.stop()   // on unmount
 */
import { computed, nextTick, ref, watch, type Ref } from 'vue'
import {
  clampFrozenColumns,
  isStickyHeaderEnabled,
  type DatabaseView,
} from '@/stores/database'

/** Left offset of each frozen column, from the widths of the header cells. */
export function frozenOffsetsFrom(
  handleWidth: number,
  columnWidths: readonly number[],
  keys: readonly string[],
  count: number,
): Record<string, number> {
  const offsets: Record<string, number> = {}
  let acc = handleWidth
  for (let i = 0; i < count && i < keys.length; i++) {
    offsets[keys[i]] = acc
    acc += columnWidths[i] ?? 0
  }
  return offsets
}

export function useStickyColumns(options: {
  activeView: Readonly<Ref<DatabaseView | null | undefined>>
  activeViewId: Readonly<Ref<string>>
  /** The columns in render order. */
  orderedColumns: Readonly<Ref<readonly { key: string }[]>>
}) {
  const { activeView, activeViewId, orderedColumns } = options

  /** The header row; its cells are measured. */
  const theadRowEl = ref<HTMLElement | null>(null)

  const stickyHeaderEnabled = computed<boolean>(() => isStickyHeaderEnabled(activeView.value))

  const frozenColumnCount = computed<number>(() => clampFrozenColumns(activeView.value?.frozenColumns))

  /** Keys of the leftmost columns to freeze, in render order. */
  const frozenColumnKeys = computed<Set<string>>(
    () => new Set(orderedColumns.value.slice(0, frozenColumnCount.value).map(c => c.key)),
  )

  /** Whether the drag-handle column is pinned (true when any column is frozen). */
  const handleFrozen = computed<boolean>(() => frozenColumnCount.value > 0)

  /** Measured left offset (px) per frozen column key. */
  const frozenOffsets = ref<Record<string, number>>({})

  function measureFrozenOffsets(): void {
    const count = frozenColumnCount.value
    if (count === 0) {
      if (Object.keys(frozenOffsets.value).length > 0) frozenOffsets.value = {}
      return
    }
    const row = theadRowEl.value
    if (!row) return
    // cells[0] = handle, cells[1..] = orderedColumns, last = add-column placeholder.
    const cells = Array.from(row.children) as HTMLElement[]
    const keys = orderedColumns.value.slice(0, count).map(c => c.key)
    const widths = keys.map((_, i) => cells[i + 1]?.offsetWidth ?? 0)
    frozenOffsets.value = frozenOffsetsFrom(cells[0]?.offsetWidth ?? 0, widths, keys, count)
  }

  /** Inline style (left offset) for a frozen data/header column cell. */
  function frozenColStyle(key: string): Record<string, string> {
    if (!frozenColumnKeys.value.has(key)) return {}
    const left = frozenOffsets.value[key]
    return left == null ? {} : { left: `${left}px` }
  }

  /** True when a column is frozen AND its offset has been measured. */
  function isFrozenCol(key: string): boolean {
    return frozenColumnKeys.value.has(key) && frozenOffsets.value[key] != null
  }

  /** Inline style for the pinned drag-handle column. */
  const handleFrozenStyle = computed<Record<string, string>>(
    () => (handleFrozen.value ? { left: '0px' } : ({} as Record<string, string>)),
  )

  // ── Observation ─────────────────────────────────────────────────────────────

  let observer: ResizeObserver | null = null

  function attachObserver(): void {
    observer?.disconnect()
    observer = null
    const el = theadRowEl.value
    if (el && typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(() => measureFrozenOffsets())
      observer.observe(el)
    }
  }

  function remeasure(): void {
    nextTick(() => { attachObserver(); measureFrozenOffsets() })
  }

  // Re-measure and re-observe whenever the rendered table structure can change.
  watch(
    () => [
      activeViewId.value,
      activeView.value?.viewType,
      frozenColumnCount.value,
      orderedColumns.value.length,
    ] as const,
    remeasure,
  )

  /** Start following the header and the window, once the table is rendered. */
  function start(): void {
    window.addEventListener('resize', measureFrozenOffsets)
    remeasure()
  }

  function stop(): void {
    window.removeEventListener('resize', measureFrozenOffsets)
    observer?.disconnect()
    observer = null
  }

  return {
    theadRowEl,
    stickyHeaderEnabled,
    handleFrozen,
    handleFrozenStyle,
    frozenColStyle,
    isFrozenCol,
    start,
    stop,
  }
}

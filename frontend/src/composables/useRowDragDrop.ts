/**
 * useRowDragDrop
 *
 * Dragging table rows by their handle. Each row has three drop zones:
 *
 *   top 25%    → above  (sibling reorder: insert before the target)
 *   middle 50% → onto   (the dragged entry becomes a sub-item of the target
 *                        through its parent_item value)
 *   bottom 25% → below  (sibling reorder: insert after the target)
 *
 * Without the sub-item pair a drop onto a row reorders like a drop below it.
 * After every drop the loaded range is loaded again.
 *
 * Usage:
 *   const rowDrag = useRowDragDrop({ databaseId, editable, parentItemSchema, rows, setParent, requery })
 *   // template: @dragstart="rowDrag.onRowDragStart($event, entry)" …
 */
import { ref, type Ref } from 'vue'
import { useDrag } from '@/composables/useDrag'
import type { DatabaseEntry, PropertySchema } from '@/stores/database'

/** Which drop zone of a row the pointer is over. */
export interface RowDropState {
  above: boolean
  below: boolean
  onto: boolean
}

const NO_DROP: RowDropState = { above: false, below: false, onto: false }

/** The drop zone at *relativeY*, the pointer's height within the row (0 to 1). */
export function dropZoneAt(relativeY: number): RowDropState {
  if (relativeY < 0.25) return { above: true, below: false, onto: false }
  if (relativeY > 0.75) return { above: false, below: true, onto: false }
  return { above: false, below: false, onto: true }
}

/**
 * The positions a row dropped next to *target* goes between: before it
 * (*above*) or after it, among *rows* in display order. Null where the list
 * ends.
 */
export function positionsAround(
  rows: readonly DatabaseEntry[],
  target: DatabaseEntry,
  above: boolean,
): [number | null, number | null] {
  const idx = rows.findIndex(r => r.id === target.id)
  if (above) return [idx > 0 ? rows[idx - 1].position : null, target.position]
  return [target.position, idx < rows.length - 1 ? rows[idx + 1].position : null]
}

export function useRowDragDrop(options: {
  databaseId: string
  editable: Readonly<Ref<boolean>>
  /** The parent_item schema of the database, once seeded. */
  parentItemSchema: Readonly<Ref<PropertySchema | null>>
  /** The rows in display order. */
  rows: Readonly<Ref<DatabaseEntry[]>>
  /** Make *childId* a sub-item of *parentId*. */
  setParent: (childId: string, parentId: string) => Promise<void>
  /** Load the rows again after a drop. */
  requery: () => Promise<void>
}) {
  const { databaseId, editable, parentItemSchema, rows, setParent, requery } = options
  const drag = useDrag()

  const rowDropStates = ref<Record<string, RowDropState>>({})
  let dragInProgress = false

  function getRowDropState(id: string): RowDropState {
    return rowDropStates.value[id] ?? NO_DROP
  }

  function clearRowDropState(id: string): void {
    delete rowDropStates.value[id]
  }

  /**
   * Whether a row drag is running or has just ended. The click that ends a
   * drag on the handle must not open the row menu.
   */
  function isRowDragInProgress(): boolean {
    return dragInProgress
  }

  function onRowDragStart(e: DragEvent, entry: DatabaseEntry): void {
    // Drags start on the row handle only, which carries draggable="true". Any
    // other drag gesture is cancelled, so it cannot interfere with clicking a
    // cell or placing the cursor.
    const handle = (e.target as HTMLElement).closest?.('.db__row-handle')
    if (!handle || !editable.value) {
      e.preventDefault()
      return
    }
    e.stopPropagation()
    e.dataTransfer!.effectAllowed = 'move'
    dragInProgress = true
    drag.startDrag(entry.id, databaseId, 'page')
  }

  function onRowDragEnd(entry: DatabaseEntry): void {
    drag.endDrag()
    clearRowDropState(entry.id)
    setTimeout(() => { dragInProgress = false }, 0)
  }

  function onRowDragOver(e: DragEvent, entry: DatabaseEntry): void {
    const { blockId, dragMode } = drag.getDragging()
    if (!blockId || dragMode === 'column' || !editable.value) return
    e.preventDefault()
    e.stopPropagation()
    e.dataTransfer!.dropEffect = 'move'
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
    rowDropStates.value[entry.id] = dropZoneAt((e.clientY - rect.top) / rect.height)
  }

  async function onRowDrop(e: DragEvent, entry: DatabaseEntry): Promise<void> {
    const { blockId: draggedId, dragMode } = drag.getDragging()
    if (!draggedId || dragMode === 'column') return
    e.preventDefault()
    e.stopPropagation()
    const state = getRowDropState(entry.id)
    clearRowDropState(entry.id)

    if (state.onto && draggedId !== entry.id) {
      if (parentItemSchema.value) {
        // A sub-item through parent_item; the blocks are not reparented.
        drag.endDrag()
        await setParent(draggedId, entry.id)
      } else {
        const [before, after] = positionsAround(rows.value, entry, false)
        await drag.dropBetween(databaseId, before, after)
      }
      await requery()
      return
    }

    const [before, after] = positionsAround(rows.value, entry, state.above)
    await drag.dropBetween(databaseId, before, after)
    await requery()
  }

  return {
    getRowDropState,
    clearRowDropState,
    isRowDragInProgress,
    onRowDragStart,
    onRowDragEnd,
    onRowDragOver,
    onRowDrop,
  }
}

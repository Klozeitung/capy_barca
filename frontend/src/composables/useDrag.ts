/**
 * useDrag – view-agnostic drag-and-drop logic for block reordering.
 *
 * This composable is intentionally decoupled from any specific view (NavView
 * or MainView). It provides the shared state and helpers that both views use
 * to communicate drag intent and resolve drop targets.
 *
 * Drop behaviour:
 * - Dropped ON a block  → inserted as last child of that block.
 * - Dropped BETWEEN two siblings → position_between(before, after).
 * - Dropped at the END of a list → position_after_last(siblings).
 *
 * Drag constraints (enforced in the respective view components):
 * - Non-page/non-workspace blocks (NavTree): may only be dropped ON pages.
 * - Page blocks (Block Editor): may only be dropped ON other pages.
 * - Workspace blocks (NavTree only): sibling reorder among workspaces only.
 *
 * Drag modes:
 * - 'block'  – normal block reordering within a BlockContentSection.
 * - 'column' – column reordering within a LayoutBlock. BlockContentSection
 *              ignores drags of this mode; LayoutBlock handles them.
 */
import { ref } from 'vue'
import { useBlockStore } from '@/stores/blocks'

export type DragMode = 'block' | 'column'

interface DragState {
  /** ID of the block being dragged, or null when not dragging. */
  blockId: string | null
  /** Parent ID at drag start, or null when not dragging. */
  sourceParentId: string | null
  /** Block type at drag start, or null when not dragging. */
  blockType: string | null
  /**
   * Drag mode: 'block' for regular block reordering, 'column' for layout
   * column reordering. Components must gate their drag handlers on this
   * field to avoid cross-mode interference.
   */
  dragMode: DragMode
}

const _dragging = ref<DragState>({
  blockId: null,
  sourceParentId: null,
  blockType: null,
  dragMode: 'block',
})

export function useDrag() {
  const blockStore = useBlockStore()

  /**
   * Begin a regular block drag operation.
   */
  function startDrag(
    blockId: string,
    sourceParentId: string | null,
    blockType: string | null = null,
  ): void {
    _dragging.value = { blockId, sourceParentId, blockType, dragMode: 'block' }
  }

  /**
   * Begin a column drag operation.
   *
   * Column drags are scoped to the containing LayoutBlock and ignored by
   * BlockContentSection drag handlers.
   *
   * @param columnId       – ID of the column block being dragged.
   * @param layoutParentId – ID of the layout block that owns the column.
   */
  function startColumnDrag(columnId: string, layoutParentId: string): void {
    _dragging.value = {
      blockId: columnId,
      sourceParentId: layoutParentId,
      blockType: 'column',
      dragMode: 'column',
    }
  }

  /**
   * End the drag operation (called on dragend regardless of drop outcome).
   */
  function endDrag(): void {
    _dragging.value = {
      blockId: null,
      sourceParentId: null,
      blockType: null,
      dragMode: 'block',
    }
  }

  function getDragging(): DragState {
    return _dragging.value
  }

  /**
   * Drop the currently dragged block ON a target block (becomes last child).
   */
  async function dropOnBlock(targetBlockId: string): Promise<void> {
    const { blockId, sourceParentId } = _dragging.value
    if (!blockId || blockId === targetBlockId) return

    const siblings = blockStore.getChildren(targetBlockId)
    const newPosition =
      siblings.length > 0 ? siblings[siblings.length - 1].position + 1.0 : 1.0

    await blockStore.moveBlock(blockId, sourceParentId, targetBlockId, newPosition)
    endDrag()
  }

  /**
   * Drop the currently dragged block BETWEEN two siblings.
   *
   * @param parentId       – Parent of both siblings.
   * @param beforePos      – Position of the block above the gap (null if at top).
   * @param afterPos       – Position of the block below the gap (null if at bottom).
   */
  async function dropBetween(
    parentId: string,
    beforePos: number | null,
    afterPos: number | null,
  ): Promise<void> {
    const { blockId, sourceParentId } = _dragging.value
    if (!blockId) return

    let newPosition: number
    if (beforePos === null && afterPos === null) {
      newPosition = 1.0
    } else if (beforePos === null) {
      newPosition = (afterPos as number) / 2.0
    } else if (afterPos === null) {
      newPosition = (beforePos as number) + 1.0
    } else {
      newPosition = (beforePos + afterPos) / 2.0
    }

    await blockStore.moveBlock(blockId, sourceParentId, parentId, newPosition)
    endDrag()
  }

  return { startDrag, startColumnDrag, endDrag, getDragging, dropOnBlock, dropBetween }
}

<script setup lang="ts">
/**
 * LayoutBlock
 *
 * Renders a layout container block as a horizontal flex row of 2–4 column
 * blocks. Column widths are stored in the parent layout block's
 * content.widths (array of proportional floats summing to 1.0).
 *
 * Features:
 * - Resize dividers: pointer-capture drag between any two adjacent columns
 *   adjusts widths live; the result is persisted on pointer-up.
 * - Column removal via ColumnBlock emit (only available when > 2 columns).
 * - Column reordering via column-mode drag-and-drop: ColumnBlock initiates
 *   the drag; LayoutBlock handles dragover/drop, scoped to its own children.
 * - Bootstrap: when mounted with fewer than 2 columns (freshly created block),
 *   missing columns are created automatically.
 * - Adding a column is available via the block-level drag handle context menu
 *   in BlockContentSection (visible when column count < 4).
 *
 * Content model:
 *   layout.content = { widths: number[] }   // proportional, sum ≈ 1.0
 *   column.content = {}                     // children managed separately
 *
 * Changes:
 * - Resize divider is now invisible by default; it only becomes visible
 *   (accent colour) on hover or during an active resize. The drag target
 *   area (8 px) is unchanged so the divider remains fully draggable.
 * - Add column affordance moved to the block-level drag handle context menu.
 */
import { computed, onMounted, ref, nextTick } from 'vue'
import { useBlockStore, type Block } from '@/stores/blocks'
import { useDrag } from '@/composables/useDrag'
import ColumnBlock from '@/components/editor/blocks/layout/ColumnBlock.vue'

// ── Props ─────────────────────────────────────────────────────────────────────

const props = defineProps<{
  block: Block
  parentId: string
}>()

// ── Dependencies ──────────────────────────────────────────────────────────────

const blockStore = useBlockStore()
const drag = useDrag()

const MIN_COLUMNS = 2

// ── Column children ───────────────────────────────────────────────────────────

const columns = computed<Block[]>(() =>
  blockStore
    .getChildren(props.block.id)
    .filter((b) => b.state === 'active' && b.type === 'column')
    .sort((a, b) => a.position - b.position),
)

// ── Width management ──────────────────────────────────────────────────────────

/**
 * Normalised widths derived from content.widths.
 * Always has the same length as columns.value and sums to 1.0.
 * Falls back to equal distribution if the stored array is missing or mismatched.
 */
const storedWidths = computed<number[]>(() => {
  const n = columns.value.length
  if (!n) return []
  const raw = props.block.content?.widths as number[] | undefined
  if (!raw || raw.length !== n) return Array(n).fill(1 / n)
  const sum = raw.reduce((a, b) => a + b, 0)
  return sum > 0 ? raw.map((w) => w / sum) : Array(n).fill(1 / n)
})

// Local copy used during an active resize; snapshot at pointer-down.
const localWidths = ref<number[]>([])
const isResizing = ref(false)
let resizeIdx = -1
let resizeStartX = 0
let resizeContainerWidth = 0
let resizeStartWidths: number[] = []

/** Widths actually used for flex values: local during resize, store otherwise. */
const displayWidths = computed<number[]>(() =>
  isResizing.value ? localWidths.value : storedWidths.value,
)

async function persistWidths(widths: number[]): Promise<void> {
  await blockStore.updateBlock(props.block.id, {
    content: { ...(props.block.content ?? {}), widths },
  })
}

// ── Bootstrap ─────────────────────────────────────────────────────────────────

onMounted(async () => {
  if (!blockStore.hasLoadedChildren(props.block.id)) {
    await blockStore.fetchChildren(props.block.id)
  }
  if (columns.value.length < MIN_COLUMNS) {
    await bootstrapColumns()
  }
})

async function bootstrapColumns(): Promise<void> {
  const needed = MIN_COLUMNS - columns.value.length
  for (let i = 0; i < needed; i++) {
    const pos = (columns.value[columns.value.length - 1]?.position ?? 0) + 1
    await blockStore.createBlock({
      type: 'column',
      parent_id: props.block.id,
      position: pos,
      content: {},
    })
  }
  await blockStore.fetchChildren(props.block.id, true)
  await nextTick()
  await persistWidths(Array(columns.value.length).fill(1 / columns.value.length))
}

// ── Remove column ─────────────────────────────────────────────────────────────

async function removeColumn(columnId: string): Promise<void> {
  if (columns.value.length <= MIN_COLUMNS) return
  await blockStore.deleteBlock(columnId, props.block.id)
  await blockStore.fetchChildren(props.block.id, true)
  await nextTick()
  const n = columns.value.length
  await persistWidths(Array(n).fill(1 / n))
}

// ── Resize dividers ───────────────────────────────────────────────────────────

function onResizeStart(e: PointerEvent, idx: number): void {
  e.preventDefault()
  ;(e.target as HTMLElement).setPointerCapture(e.pointerId)
  isResizing.value = true
  resizeIdx = idx
  resizeStartX = e.clientX
  localWidths.value = [...storedWidths.value]
  resizeStartWidths = [...storedWidths.value]
  const row = (e.target as HTMLElement).closest('.layout-block__row') as HTMLElement | null
  resizeContainerWidth = row?.offsetWidth ?? 800
}

function onResizeMove(e: PointerEvent): void {
  if (!isResizing.value || resizeIdx < 0) return
  const MIN_WIDTH = 0.1
  const delta = (e.clientX - resizeStartX) / resizeContainerWidth
  const left = resizeStartWidths[resizeIdx] + delta
  const right = resizeStartWidths[resizeIdx + 1] - delta
  if (left < MIN_WIDTH || right < MIN_WIDTH) return
  const next = [...resizeStartWidths]
  next[resizeIdx] = left
  next[resizeIdx + 1] = right
  localWidths.value = next
}

async function onResizeEnd(e: PointerEvent): Promise<void> {
  if (!isResizing.value) return
  ;(e.target as HTMLElement).releasePointerCapture(e.pointerId)
  isResizing.value = false
  await persistWidths([...localWidths.value])
  resizeIdx = -1
}

// ── Column drag & drop (reordering) ──────────────────────────────────────────

interface ColDropState { left: boolean; right: boolean }

const colDropStates = ref<Record<string, ColDropState>>({})

function getColDropState(id: string): ColDropState {
  return colDropStates.value[id] ?? { left: false, right: false }
}

function clearColDropState(id: string): void {
  delete colDropStates.value[id]
}

function onColDragOver(e: DragEvent, col: Block): void {
  const { dragMode, blockId, sourceParentId } = drag.getDragging()
  // Only handle column-mode drags originating from this same layout block.
  if (dragMode !== 'column' || !blockId || sourceParentId !== props.block.id) return
  e.preventDefault()
  e.stopPropagation()
  e.dataTransfer!.dropEffect = 'move'
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
  const mid = rect.left + rect.width / 2
  colDropStates.value[col.id] =
    e.clientX < mid ? { left: true, right: false } : { left: false, right: true }
}

function onColDragLeave(colId: string): void {
  clearColDropState(colId)
}

async function onColDrop(e: DragEvent, col: Block): Promise<void> {
  const { dragMode, blockId, sourceParentId } = drag.getDragging()
  if (dragMode !== 'column' || !blockId || sourceParentId !== props.block.id) return
  e.preventDefault()
  e.stopPropagation()

  const state = getColDropState(col.id)
  const cols = columns.value
  const srcIdx = cols.findIndex((c) => c.id === blockId)
  const tgtIdx = cols.findIndex((c) => c.id === col.id)

  colDropStates.value = {}

  if (srcIdx === -1 || tgtIdx === -1 || srcIdx === tgtIdx) {
    drag.endDrag()
    return
  }

  let newPosition: number
  if (state.left) {
    const beforePos = tgtIdx > 0 ? cols[tgtIdx - 1].position : null
    newPosition =
      beforePos === null
        ? cols[tgtIdx].position / 2
        : (beforePos + cols[tgtIdx].position) / 2
  } else {
    const afterPos = tgtIdx < cols.length - 1 ? cols[tgtIdx + 1].position : null
    newPosition =
      afterPos === null
        ? cols[tgtIdx].position + 1
        : (cols[tgtIdx].position + afterPos) / 2
  }

  await blockStore.moveBlock(blockId, props.block.id, props.block.id, newPosition)
  drag.endDrag()
  await blockStore.fetchChildren(props.block.id, true)
}
</script>

<template>
  <div class="layout-block">
    <!-- Resize pointer events are captured on this row element. -->
    <div
      class="layout-block__row"
      :class="{ 'layout-block__row--resizing': isResizing }"
      @pointermove="onResizeMove"
      @pointerup="onResizeEnd"
    >
      <template v-for="(col, idx) in columns" :key="col.id">
        <!-- Column wrapper: carries the flex width and column-drop states. -->
        <div
          class="layout-block__col-wrapper"
          :style="{ flex: displayWidths[idx] ?? (1 / columns.length) }"
          :class="{
            'layout-block__col-wrapper--drop-left': getColDropState(col.id).left,
            'layout-block__col-wrapper--drop-right': getColDropState(col.id).right,
          }"
          @dragover="(e) => onColDragOver(e, col)"
          @dragleave="onColDragLeave(col.id)"
          @drop="(e) => onColDrop(e, col)"
        >
          <ColumnBlock
            :block="col"
            :parent-id="block.id"
            :can-remove="columns.length > MIN_COLUMNS"
            @remove="removeColumn"
          />
        </div>

        <!-- Resize divider between adjacent columns.
             Invisible by default — only the 8 px drag-target area is present.
             The ::after indicator appears on hover and during resize. -->
        <div
          v-if="idx < columns.length - 1"
          class="layout-block__resize-divider"
          @pointerdown="(e) => onResizeStart(e, idx)"
        />
      </template>
    </div>
  </div>
</template>

<style scoped>
.layout-block {
  flex: 1;
  min-width: 0;
  width: 100%;
}

/* ── Column row ──────────────────────────────────────────────────────────── */
.layout-block__row {
  display: flex;
  align-items: stretch;
  width: 100%;
  touch-action: none;
  user-select: none;
}

.layout-block__row--resizing {
  cursor: col-resize;
}

/* ── Column wrapper ──────────────────────────────────────────────────────── */
.layout-block__col-wrapper {
  min-width: 0;
  position: relative;
}

.layout-block__col-wrapper--drop-left {
  border-left: 2px solid var(--color-accent);
}

.layout-block__col-wrapper--drop-right {
  border-right: 2px solid var(--color-accent);
}

/* ── Resize divider ──────────────────────────────────────────────────────── */
/*
 * DELIBERATE DESIGN — divider is invisible by default (do not revert):
 * The 8 px drag-target area is always present and fully interactive.
 * The ::after indicator line starts transparent so adjacent columns have
 * no visual border between them. It appears only on hover or during an
 * active resize, giving a clean Notion-like appearance while still making
 * the drag affordance discoverable.
 */
.layout-block__resize-divider {
  flex: 0 0 8px;
  position: relative;
  cursor: col-resize;
  z-index: 1;
  display: flex;
  align-items: stretch;
}

/* Visual indicator line — invisible until hovered or resizing */
.layout-block__resize-divider::after {
  content: '';
  position: absolute;
  left: 50%;
  top: 0;
  bottom: 0;
  width: 2px;
  transform: translateX(-50%);
  border-radius: 1px;
  background: transparent;
  transition: background 0.15s, width 0.1s;
}

.layout-block__resize-divider:hover::after,
.layout-block__row--resizing .layout-block__resize-divider::after {
  background: var(--color-accent);
  width: 3px;
}

@media print {
  .layout-block__resize-divider {
    display: none !important;
  }
}
</style>

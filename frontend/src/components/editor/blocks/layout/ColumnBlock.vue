<script setup lang="ts">
/**
 * ColumnBlock
 *
 * Renders a single column inside a LayoutBlock. Provides:
 * - A column-level toolbar (visible on hover) with a drag handle and an
 *   optional remove button.
 * - A nested BlockContentSection for the column's block children.
 *
 * Drag mode: the drag handle initiates a column-mode drag (dragMode = 'column').
 * This is distinct from a regular block drag so that BlockContentSection does
 * not misinterpret it as a block reorder event.
 *
 * The remove button is only shown when canRemove is true (i.e. the layout has
 * more than 2 columns). Deletion is delegated to LayoutBlock via the `remove`
 * emit to keep width management centralised there.
 */
import { useI18n } from 'vue-i18n'
import { Icon } from '@iconify/vue'
import { type Block } from '@/stores/blocks'
import { useDrag } from '@/composables/useDrag'
import BlockContentSection from '@/components/main/BlockContentSection.vue'

// ── Props & Emits ─────────────────────────────────────────────────────────────

const props = defineProps<{
  block: Block
  /** ID of the parent layout block. */
  parentId: string
  /** Whether a remove button should be shown (false when only 2 columns remain). */
  canRemove: boolean
}>()

const emit = defineEmits<{
  /** Inform LayoutBlock that this column should be removed. */
  (e: 'remove', columnId: string): void
}>()

// ── Dependencies ──────────────────────────────────────────────────────────────

const { t } = useI18n()
const drag = useDrag()

// ── Column drag (reordering within LayoutBlock) ───────────────────────────────

function onColumnDragStart(e: DragEvent): void {
  // Prevent the event from bubbling up to the content-block wrapper in
  // BlockContentSection, which would start a competing block-mode drag.
  e.stopPropagation()
  e.dataTransfer!.effectAllowed = 'move'
  drag.startColumnDrag(props.block.id, props.parentId)
}

function onColumnDragEnd(): void {
  drag.endDrag()
}
</script>

<template>
  <div class="column-block">
    <!-- Toolbar: always in the DOM for layout stability; made visible on hover
         via CSS. Kept outside BlockContentSection so it doesn't interfere with
         the content area's drag events. -->
    <div class="column-block__toolbar">
      <!-- Drag handle: draggable="true" scoped to the handle span only so
           the rest of the column does not accidentally initiate a column drag. -->
      <span
        class="column-block__drag-handle"
        draggable="true"
        :title="t('block.types.column')"
        @dragstart="onColumnDragStart"
        @dragend="onColumnDragEnd"
      >
        <Icon icon="mdi:drag-horizontal-variant" width="14" height="14" />
      </span>

      <button
        v-if="canRemove"
        class="column-block__remove-btn"
        :title="t('layout.removeColumn')"
        @click="emit('remove', block.id)"
      >
        <Icon icon="mdi:close" width="12" height="12" />
      </button>
    </div>

    <!-- Column content: full BlockContentSection so any block type can live
         inside a column. The nested prop removes extra padding. -->
    <BlockContentSection :parent-id="block.id" :nested="true" />
  </div>
</template>

<style scoped>
/*
 * DELIBERATE DESIGN — no visible column borders (do not revert):
 * Layout blocks intentionally have no border so users can create seamless
 * multi-column layouts without visual grid noise. The resize divider between
 * columns provides sufficient spatial cue. Removing the border also lets
 * users build borderless card-style layouts with full control over aesthetics.
 */
.column-block {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 80px;
  overflow: hidden;
}

/* ── Toolbar ─────────────────────────────────────────────────────────────── */
/*
 * No border-bottom: without a column border, the toolbar separator would
 * look orphaned. The hover-triggered opacity transition is sufficient to
 * communicate the toolbar affordance without a permanent visual line.
 */
.column-block__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 2px 4px;
  min-height: 22px;
  background: var(--color-surface);
  opacity: 0;
  transition: opacity 0.1s;
}

/* Show toolbar when the column itself is hovered. Using :hover on the parent
   keeps the toolbar visible while the pointer moves to a toolbar button. */
.column-block:hover .column-block__toolbar {
  opacity: 1;
}

/* ── Drag handle ─────────────────────────────────────────────────────────── */
.column-block__drag-handle {
  display: flex;
  align-items: center;
  cursor: grab;
  color: var(--color-text-muted);
  padding: 2px 4px;
  border-radius: 3px;
  transition: background 0.1s, color 0.1s;
  user-select: none;
}

.column-block__drag-handle:hover {
  background: var(--color-hover);
  color: var(--color-text);
}

.column-block__drag-handle:active {
  cursor: grabbing;
}

/* ── Remove button ───────────────────────────────────────────────────────── */
.column-block__remove-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  padding: 0;
  border: none;
  border-radius: 3px;
  background: transparent;
  color: var(--color-text-muted);
  cursor: pointer;
  transition: background 0.1s, color 0.1s;
}

.column-block__remove-btn:hover {
  background: var(--color-danger-subtle, rgba(211, 47, 47, 0.07));
  color: var(--color-danger, #d32f2f);
}

@media print {
  .column-block {
    overflow: visible !important;
    height: auto !important;
    min-height: 0 !important;
  }

  .column-block__toolbar {
    display: none !important;
  }
}
</style>

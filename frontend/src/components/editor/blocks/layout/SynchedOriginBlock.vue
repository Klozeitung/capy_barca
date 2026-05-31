<script setup lang="ts">
/**
 * SynchedOriginBlock
 *
 * Container block whose children are shared with all synched_mirror blocks
 * that reference it. Duplicating this block creates a synched_mirror rather
 * than copying the subtree.
 *
 * Renders its own children via a nested BlockContentSection. No visible
 * header is shown here; the block type is surfaced in the drag-handle
 * context menu by BlockContentSection.
 */
import { type Block } from '@/stores/blocks'
import BlockContentSection from '@/components/main/BlockContentSection.vue'

// ── Props ─────────────────────────────────────────────────────────────────────

defineProps<{
  block: Block
  parentId: string
}>()
</script>

<template>
  <div class="synched-origin">
    <BlockContentSection :parent-id="block.id" :nested="true" />
  </div>
</template>

<style scoped>
.synched-origin {
  width: 100%;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  padding: 4px 0 2px;
}
</style>

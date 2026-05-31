<script setup lang="ts">
/**
 * SynchedMirrorBlock
 *
 * Renders the children of the referenced synched_origin block by passing the
 * origin's ID as parentId to BlockContentSection.  Any edits made through an
 * unlocked mirror are therefore edits to the origin's actual children — this
 * is the "remote" behaviour and comes for free because the parentId is the
 * origin's ID.
 *
 * Lock state (block.content.locked)
 * ----------------------------------
 * When locked, the children section is wrapped in a pointer-events: none
 * overlay so no interaction is possible.  The block itself (drag handle,
 * context menu) remains operable so the lock can be toggled or the mirror
 * can be moved/deleted.
 *
 * Broken reference
 * ----------------
 * When reference_id is null (origin was deleted), a fallback message is
 * shown instead of the children section.
 *
 * No visible header badge is rendered here; the block type and Lock toggle
 * are surfaced in the drag-handle context menu by BlockContentSection.
 */
import { computed } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { type Block } from '@/stores/blocks'
import BlockContentSection from '@/components/main/BlockContentSection.vue'

// ── Props ─────────────────────────────────────────────────────────────────────

const props = defineProps<{
  block: Block
  parentId: string
}>()

// ── Dependencies ──────────────────────────────────────────────────────────────

const { t } = useI18n()

// ── Derived state ─────────────────────────────────────────────────────────────

const originId = computed<string | null>(() => props.block.reference_id ?? null)

const isLocked = computed<boolean>(
  () => (props.block.content?.locked as boolean | undefined) ?? false,
)
</script>

<template>
  <div class="synched-mirror" :class="{ 'synched-mirror--locked': isLocked }">
    <template v-if="originId">
      <div
        class="synched-mirror__body"
        :class="{ 'synched-mirror__body--locked': isLocked }"
      >
        <BlockContentSection :parent-id="originId" :nested="true" />
      </div>
    </template>
    <div v-else class="synched-mirror__broken">
      <Icon icon="mdi:link-off" width="13" height="13" />
      {{ t('synchedBlock.originNotFound') }}
    </div>
  </div>
</template>

<style scoped>
.synched-mirror {
  width: 100%;
  border: 1px solid var(--color-border);
  border-radius: 6px;
}

.synched-mirror--locked {
  opacity: 0.85;
}

.synched-mirror__body {
  padding: 4px 0 2px;
}

/* Locked: disable all pointer interaction inside the content area. */
.synched-mirror__body--locked {
  pointer-events: none;
}

.synched-mirror__broken {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 12px;
  font-size: 0.8125rem;
  color: var(--color-text-muted);
  font-style: italic;
}
</style>

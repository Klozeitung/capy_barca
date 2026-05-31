<script setup lang="ts">
/**
 * TrashModal
 *
 * Displays all top-level trashed blocks (GET /api/blocks/trash) with
 * individual restore / permanent-delete actions and a global "Empty" button.
 *
 * The backend already provides the full infrastructure (soft_delete, restore,
 * purge endpoints and WS state_changed / purged events). This component is
 * the missing frontend surface for issue #35.
 */
import { ref, computed, onMounted } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { useBlockStore, type Block } from '@/stores/blocks'

const emit = defineEmits<{
  (e: 'close'): void
}>()

const { t } = useI18n()
const blockStore = useBlockStore()

// ── State ─────────────────────────────────────────────────────────────────────

const trashed = ref<Block[]>([])
const isLoading = ref(true)
const confirmingEmptyAll = ref(false)
const confirmingPurgeId = ref<string | null>(null)
const processingId = ref<string | null>(null)

// ── Bootstrap ─────────────────────────────────────────────────────────────────

onMounted(async () => {
  await load()
})

async function load(): Promise<void> {
  isLoading.value = true
  try {
    trashed.value = await blockStore.fetchTrashed()
  } finally {
    isLoading.value = false
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const isEmpty = computed(() => !isLoading.value && trashed.value.length === 0)

function blockTitle(block: Block): string {
  return (block.content?.title as string | undefined) || t('nav.untitled')
}

function blockIcon(block: Block): string {
  if (block.icon) return block.icon
  switch (block.type) {
    case 'database': return 'mdi:table'
    case 'calendar': return 'mdi:calendar-outline'
    default:         return 'mdi:file-document-outline'
  }
}

function typeLabel(block: Block): string {
  const key = `block.types.${block.type}`
  const label = t(key)
  return label !== key ? label : block.type
}

// ── Actions ───────────────────────────────────────────────────────────────────

async function restore(block: Block): Promise<void> {
  if (processingId.value) return
  processingId.value = block.id
  try {
    await blockStore.restoreBlock(block.id)
    trashed.value = trashed.value.filter((b) => b.id !== block.id)
  } finally {
    processingId.value = null
  }
}

async function purge(block: Block, e: MouseEvent): Promise<void> {
  // Ctrl+Click (or Cmd+Click on Mac) skips the confirmation prompt
  if (!e.ctrlKey && !e.metaKey) {
    confirmingPurgeId.value = block.id
    return
  }
  await _doPurge(block.id)
}

async function confirmPurge(blockId: string): Promise<void> {
  confirmingPurgeId.value = null
  await _doPurge(blockId)
}

async function _doPurge(blockId: string): Promise<void> {
  if (processingId.value) return
  processingId.value = blockId
  try {
    await blockStore.purgeBlock(blockId)
    trashed.value = trashed.value.filter((b) => b.id !== blockId)
  } finally {
    processingId.value = null
  }
}

async function emptyAll(): Promise<void> {
  confirmingEmptyAll.value = false
  const toDelete = [...trashed.value]
  for (const block of toDelete) {
    await blockStore.purgeBlock(block.id)
  }
  trashed.value = []
}
</script>

<template>
  <div class="trash-backdrop" @mousedown.self="emit('close')">
    <div class="trash-modal" role="dialog" :aria-label="t('trash.title')">

      <!-- ── Header ────────────────────────────────────────────────────────── -->
      <div class="trash-modal__header">
        <Icon icon="mdi:trash-can-outline" width="16" height="16" class="trash-modal__header-icon" />
        <span class="trash-modal__title">{{ t('trash.title') }}</span>
        <button
          v-if="trashed.length > 0 && !confirmingEmptyAll"
          class="trash-modal__empty-btn"
          @click="confirmingEmptyAll = true"
        >
          {{ t('trash.emptyAll') }}
        </button>
        <button class="trash-modal__close" @click="emit('close')">
          <Icon icon="mdi:close" width="15" height="15" />
        </button>
      </div>

      <!-- ── Confirm empty all ──────────────────────────────────────────────── -->
      <div v-if="confirmingEmptyAll" class="trash-modal__confirm-banner">
        <span>{{ t('trash.confirmEmptyHint') }}</span>
        <div class="trash-modal__confirm-actions">
          <button class="trash-modal__confirm-cancel" @click="confirmingEmptyAll = false">
            {{ t('actions.cancel') }}
          </button>
          <button class="trash-modal__confirm-ok" @click="emptyAll">
            {{ t('trash.confirmEmptyBtn') }}
          </button>
        </div>
      </div>

      <!-- ── Body ──────────────────────────────────────────────────────────── -->
      <div class="trash-modal__body">

        <!-- Loading -->
        <div v-if="isLoading" class="trash-modal__loading">
          <Icon icon="mdi:loading" width="20" height="20" class="trash-modal__spinner" />
        </div>

        <!-- Empty state -->
        <div v-else-if="isEmpty" class="trash-modal__empty">
          <Icon icon="mdi:trash-can-outline" width="32" height="32" class="trash-modal__empty-icon" />
          <span>{{ t('trash.empty') }}</span>
        </div>

        <!-- Item list -->
        <div v-else class="trash-modal__list">
          <div
            v-for="block in trashed"
            :key="block.id"
            class="trash-item"
            :class="{ 'trash-item--processing': processingId === block.id }"
          >
            <Icon :icon="blockIcon(block)" width="16" height="16" class="trash-item__icon" />
            <span class="trash-item__title">{{ blockTitle(block) }}</span>
            <span class="trash-item__type">{{ typeLabel(block) }}</span>

            <!-- Inline purge confirmation -->
            <template v-if="confirmingPurgeId === block.id">
              <div class="trash-item__confirm">
                <span class="trash-item__confirm-label">{{ t('trash.confirmDeleteHint') }}</span>
                <button class="trash-item__btn" @click="confirmingPurgeId = null">
                  {{ t('actions.cancel') }}
                </button>
                <button class="trash-item__btn trash-item__btn--danger" @click="confirmPurge(block.id)">
                  {{ t('trash.deletePermanently') }}
                </button>
              </div>
            </template>

            <!-- Normal actions -->
            <template v-else>
              <div class="trash-item__actions">
                <button
                  class="trash-item__btn"
                  :disabled="processingId !== null"
                  :title="t('trash.restore')"
                  @click="restore(block)"
                >
                  <Icon icon="mdi:restore" width="14" height="14" />
                  {{ t('trash.restore') }}
                </button>
                <button
                  class="trash-item__btn trash-item__btn--danger"
                  :disabled="processingId !== null"
                  :title="t('trash.deletePermanentlyHint')"
                  @click="purge(block, $event)"
                >
                  <Icon icon="mdi:delete-forever-outline" width="14" height="14" />
                </button>
              </div>
            </template>
          </div>
        </div>

      </div>
    </div>
  </div>
</template>

<style scoped>
.trash-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 300;
}

.trash-modal {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  width: min(520px, 94vw);
  max-height: 70vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.28);
}

/* ── Header ──────────────────────────────────────────────────────────────── */
.trash-modal__header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 13px 16px;
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.trash-modal__header-icon {
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.trash-modal__title {
  flex: 1;
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--color-text);
}

.trash-modal__empty-btn {
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: 5px;
  padding: 4px 10px;
  font-size: 0.78rem;
  color: var(--color-text-muted);
  cursor: pointer;
  transition: color 0.15s, border-color 0.15s;
}

.trash-modal__empty-btn:hover {
  color: #e05353;
  border-color: #e05353;
}

.trash-modal__close {
  background: transparent;
  border: none;
  cursor: pointer;
  color: var(--color-text-muted);
  display: flex;
  align-items: center;
  padding: 3px;
  border-radius: 4px;
  transition: color 0.15s, background 0.15s;
}

.trash-modal__close:hover {
  color: var(--color-text);
  background: var(--color-hover);
}

/* ── Confirm banner ──────────────────────────────────────────────────────── */
.trash-modal__confirm-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 16px;
  background: rgba(224, 83, 83, 0.08);
  border-bottom: 1px solid rgba(224, 83, 83, 0.2);
  font-size: 0.8125rem;
  color: var(--color-text);
  flex-shrink: 0;
}

.trash-modal__confirm-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.trash-modal__confirm-cancel,
.trash-modal__confirm-ok {
  padding: 4px 12px;
  border-radius: 5px;
  font-size: 0.8rem;
  cursor: pointer;
  border: 1px solid var(--color-border);
  transition: background 0.1s;
}

.trash-modal__confirm-cancel {
  background: var(--color-surface);
  color: var(--color-text-muted);
}

.trash-modal__confirm-cancel:hover {
  background: var(--color-hover);
}

.trash-modal__confirm-ok {
  background: #e05353;
  color: #fff;
  border-color: #e05353;
}

.trash-modal__confirm-ok:hover {
  background: #c94040;
}

/* ── Body ────────────────────────────────────────────────────────────────── */
.trash-modal__body {
  flex: 1;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: var(--color-border) transparent;
}

.trash-modal__loading {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px;
}

.trash-modal__spinner {
  animation: spin 0.8s linear infinite;
  color: var(--color-text-muted);
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.trash-modal__empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 48px 20px;
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.trash-modal__empty-icon {
  opacity: 0.3;
}

/* ── Item list ───────────────────────────────────────────────────────────── */
.trash-modal__list {
  display: flex;
  flex-direction: column;
}

.trash-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--color-border);
  transition: background 0.1s, opacity 0.15s;
}

.trash-item:last-child {
  border-bottom: none;
}

.trash-item:hover {
  background: var(--color-hover);
}

.trash-item--processing {
  opacity: 0.5;
  pointer-events: none;
}

.trash-item__icon {
  flex-shrink: 0;
  color: var(--color-text-muted);
}

.trash-item__title {
  flex: 1;
  font-size: 0.875rem;
  color: var(--color-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.trash-item__type {
  font-size: 0.72rem;
  color: var(--color-text-muted);
  background: var(--color-hover);
  border-radius: 3px;
  padding: 1px 6px;
  white-space: nowrap;
  flex-shrink: 0;
}

.trash-item__actions {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}

.trash-item__confirm {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.trash-item__confirm-label {
  font-size: 0.775rem;
  color: var(--color-text-muted);
  white-space: nowrap;
}

.trash-item__btn {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 4px 9px;
  border: 1px solid var(--color-border);
  border-radius: 5px;
  background: var(--color-surface);
  color: var(--color-text-muted);
  font-size: 0.775rem;
  cursor: pointer;
  transition: background 0.1s, color 0.1s, border-color 0.1s;
}

.trash-item__btn:hover:not(:disabled) {
  background: var(--color-hover);
  color: var(--color-text);
}

.trash-item__btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.trash-item__btn--danger:hover:not(:disabled) {
  background: rgba(224, 83, 83, 0.1);
  color: #e05353;
  border-color: rgba(224, 83, 83, 0.4);
}
</style>

<script setup lang="ts">
/**
 * LinkedDatabasePicker
 *
 * Dropdown that lists all available databases in the workspace so the user
 * can pick one when inserting a linked_database block via the slash menu.
 *
 * Positioning mirrors SlashMenu: fixed, anchored below the trigger rect,
 * flipped above when there is not enough space below the viewport edge.
 *
 * Emits:
 *   select(dbId)  – user confirmed a database choice
 *   close         – user dismissed without choosing (click-outside, Escape)
 */
import { ref, computed, onMounted } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { useDatabaseStore } from '@/stores/database'

// ── Props & Emits ─────────────────────────────────────────────────────────────

const props = defineProps<{
  anchorRect: DOMRect | null
}>()

const emit = defineEmits<{
  (e: 'select', dbId: string): void
  (e: 'close'): void
}>()

// ── Dependencies ──────────────────────────────────────────────────────────────

const { t } = useI18n()
const dbStore = useDatabaseStore()
const isLoading = ref(true)

onMounted(async () => {
  await dbStore.fetchAllDatabases()
  isLoading.value = false
})

// ── Positioning ───────────────────────────────────────────────────────────────

const MENU_WIDTH = 260
const MENU_HEIGHT_ESTIMATE = 220

const style = computed(() => {
  const rect = props.anchorRect
  if (!rect) return {}

  const viewportWidth  = window.innerWidth
  const viewportHeight = window.innerHeight

  let left = rect.left
  if (left + MENU_WIDTH > viewportWidth - 8) {
    left = Math.max(8, viewportWidth - MENU_WIDTH - 8)
  }

  const spaceBelow = viewportHeight - rect.bottom - 8
  const top =
    spaceBelow >= MENU_HEIGHT_ESTIMATE
      ? rect.bottom + 4
      : Math.max(8, rect.top - MENU_HEIGHT_ESTIMATE - 4)

  return {
    top:   `${top}px`,
    left:  `${left}px`,
    width: `${MENU_WIDTH}px`,
  }
})
</script>

<template>
  <Teleport to="body">
    <div
      class="ldb-picker"
      :style="style"
      role="listbox"
      :aria-label="t('linkedDb.pickerTitle')"
      @mousedown.stop
      @click.stop
    >
      <p class="ldb-picker__header">{{ t('linkedDb.pickerTitle') }}</p>

      <div v-if="isLoading" class="ldb-picker__loading">
        <Icon icon="mdi:loading" class="ldb-picker__spinner" width="16" height="16" />
      </div>

      <ul v-else-if="dbStore.allDatabases.length" class="ldb-picker__list">
        <li
          v-for="db in dbStore.allDatabases"
          :key="db.id"
          class="ldb-picker__item"
          role="option"
          tabindex="-1"
          @mousedown.prevent="emit('select', db.id)"
        >
          <Icon icon="mdi:table-large" width="14" height="14" class="ldb-picker__item-icon" />
          <span class="ldb-picker__item-label">{{ db.title ?? t('main.untitled') }}</span>
        </li>
      </ul>

      <p v-else class="ldb-picker__empty">{{ t('linkedDb.pickerEmpty') }}</p>
    </div>
  </Teleport>
</template>

<style scoped>
.ldb-picker {
  position: fixed;
  z-index: 1000;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
  padding: 4px;
  max-height: 280px;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: var(--color-border) transparent;
}

.ldb-picker__header {
  padding: 8px 8px 4px;
  font-size: 0.65rem;
  font-weight: 700;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  color: var(--color-text-muted);
  user-select: none;
  margin: 0;
}

.ldb-picker__loading {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
}

@keyframes ldb-spin {
  to { transform: rotate(360deg); }
}
.ldb-picker__spinner {
  animation: ldb-spin 0.7s linear infinite;
  color: var(--color-text-muted);
}

.ldb-picker__list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.ldb-picker__item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 8px;
  border-radius: 5px;
  cursor: pointer;
  color: var(--color-text);
  font-size: 0.875rem;
  transition: background 0.1s;
}

.ldb-picker__item:hover {
  background: var(--color-hover);
}

.ldb-picker__item-icon {
  flex-shrink: 0;
  color: var(--color-text-muted);
}

.ldb-picker__item-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ldb-picker__empty {
  padding: 12px 8px;
  font-size: 0.875rem;
  color: var(--color-text-muted);
  text-align: center;
  margin: 0;
}
</style>

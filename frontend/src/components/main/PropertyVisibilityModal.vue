<script setup lang="ts">
/**
 * PropertyVisibilityModal
 *
 * Per-database modal that controls which properties appear in the
 * BlockPropertySection (SideView and whole-page entry view).
 *
 * Three visibility modes per schema:
 * - ``show``       – always visible
 * - ``hide_empty`` – visible only when the entry has a non-null / non-empty value
 * - ``hide``       – always hidden
 *
 * The map is persisted as a block preference under key
 * ``property_sideview_visibility`` on the database block.
 * Schemas not present in the map default to ``show``.
 */
import { ref, computed, onMounted } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { useBlockStore } from '@/stores/blocks'
import { useDatabaseStore, type PropertySchema } from '@/stores/database'
import { getSchemaIcon } from '@/stores/propertyTypes'

// ── Constants ─────────────────────────────────────────────────────────────────

export type VisibilityMode = 'show' | 'hide_empty' | 'hide'

const PREF_KEY = 'property_sideview_visibility'

const MODES: { key: VisibilityMode; icon: string; labelKey: string }[] = [
  { key: 'show',       icon: 'mdi:eye-outline',         labelKey: 'propertySection.visibility.show' },
  { key: 'hide_empty', icon: 'mdi:eye-off-outline',     labelKey: 'propertySection.visibility.hideEmpty' },
  { key: 'hide',       icon: 'mdi:eye-remove-outline',  labelKey: 'propertySection.visibility.hide' },
]

// ── Props / emits ─────────────────────────────────────────────────────────────

const props = defineProps<{
  databaseId: string
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'update', map: Record<string, VisibilityMode>): void
}>()

// ── Dependencies ──────────────────────────────────────────────────────────────

const { t } = useI18n()
const blockStore = useBlockStore()
const dbStore = useDatabaseStore()

// ── State ─────────────────────────────────────────────────────────────────────

const draft = ref<Record<string, VisibilityMode>>({})

const schemas = computed<PropertySchema[]>(() =>
  dbStore.getSchemas(props.databaseId).filter((s) => s.name !== '__name__'),
)

onMounted(() => {
  const saved = blockStore.getPreference<Record<string, VisibilityMode>>(
    props.databaseId,
    PREF_KEY,
    {},
  )
  draft.value = { ...saved }
})

function getMode(schemaId: string): VisibilityMode {
  return draft.value[schemaId] ?? 'show'
}

function cycleMode(schemaId: string): void {
  const current = getMode(schemaId)
  const order: VisibilityMode[] = ['show', 'hide_empty', 'hide']
  const next = order[(order.indexOf(current) + 1) % order.length]
  draft.value[schemaId] = next
}

function modeInfo(mode: VisibilityMode) {
  return MODES.find((m) => m.key === mode)!
}

// ── Bulk actions ──────────────────────────────────────────────────────────────

function setAll(mode: VisibilityMode): void {
  const newDraft: Record<string, VisibilityMode> = {}
  for (const s of schemas.value) {
    newDraft[s.id] = mode
  }
  draft.value = newDraft
}

// ── Save / cancel ─────────────────────────────────────────────────────────────

async function save(): Promise<void> {
  // Remove entries that are 'show' (the default) to keep the preference lean.
  const cleaned: Record<string, VisibilityMode> = {}
  for (const [id, mode] of Object.entries(draft.value)) {
    if (mode !== 'show') cleaned[id] = mode
  }
  await blockStore.setPreference(props.databaseId, PREF_KEY, cleaned)
  emit('update', cleaned)
  emit('close')
}
</script>

<template>
  <div class="pvm-backdrop" @mousedown.self="emit('close')">
    <div class="pvm" role="dialog" :aria-label="t('propertySection.visibility.title')">

      <!-- Header -->
      <div class="pvm__header">
        <span class="pvm__title">{{ t('propertySection.visibility.title') }}</span>
        <button class="pvm__close" @click="emit('close')" :aria-label="t('actions.cancel')">
          <Icon icon="mdi:close" width="15" height="15" />
        </button>
      </div>

      <!-- Bulk actions -->
      <div class="pvm__bulk">
        <button class="pvm__bulk-btn" @click="setAll('show')">
          <Icon icon="mdi:eye-outline" width="13" height="13" />
          {{ t('propertySection.visibility.showAll') }}
        </button>
        <button class="pvm__bulk-btn" @click="setAll('hide_empty')">
          <Icon icon="mdi:eye-off-outline" width="13" height="13" />
          {{ t('propertySection.visibility.hideAllEmpty') }}
        </button>
        <button class="pvm__bulk-btn" @click="setAll('hide')">
          <Icon icon="mdi:eye-remove-outline" width="13" height="13" />
          {{ t('propertySection.visibility.hideAll') }}
        </button>
      </div>

      <!-- Schema list -->
      <div class="pvm__list">
        <div
          v-for="schema in schemas"
          :key="schema.id"
          class="pvm__row"
        >
          <Icon :icon="getSchemaIcon(schema)" width="14" height="14" class="pvm__type-icon" />
          <span class="pvm__name">{{ schema.name }}</span>
          <button
            class="pvm__mode-btn"
            :class="`pvm__mode-btn--${getMode(schema.id)}`"
            :title="t(modeInfo(getMode(schema.id)).labelKey)"
            @click="cycleMode(schema.id)"
          >
            <Icon :icon="modeInfo(getMode(schema.id)).icon" width="15" height="15" />
            <span class="pvm__mode-label">{{ t(modeInfo(getMode(schema.id)).labelKey) }}</span>
          </button>
        </div>
      </div>

      <!-- Footer -->
      <div class="pvm__footer">
        <button class="pvm__cancel-btn" @click="emit('close')">
          {{ t('actions.cancel') }}
        </button>
        <button class="pvm__save-btn" @click="save">
          {{ t('actions.save') }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.pvm-backdrop {
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: rgba(0, 0, 0, 0.35);
  display: flex;
  align-items: center;
  justify-content: center;
}

.pvm {
  background: var(--color-bg-surface, var(--color-bg));
  border: 1px solid var(--color-border);
  border-radius: 10px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.18);
  width: 420px;
  max-height: 80vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* ── Header ─────────────────────────────────────────────────────────────── */

.pvm__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px 10px;
  border-bottom: 1px solid var(--color-border);
}

.pvm__title {
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--color-text);
}

.pvm__close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  border-radius: 5px;
  background: transparent;
  cursor: pointer;
  color: var(--color-text-muted);
  transition: background 0.1s, color 0.1s;
}

.pvm__close:hover {
  background: var(--color-hover);
  color: var(--color-text);
}

/* ── Bulk actions ───────────────────────────────────────────────────────── */

.pvm__bulk {
  display: flex;
  gap: 6px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--color-border);
}

.pvm__bulk-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  background: transparent;
  cursor: pointer;
  font-size: 0.72rem;
  color: var(--color-text-muted);
  transition: background 0.1s, color 0.1s, border-color 0.1s;
}

.pvm__bulk-btn:hover {
  background: var(--color-hover);
  color: var(--color-text);
  border-color: var(--color-text-muted);
}

/* ── Schema list ────────────────────────────────────────────────────────── */

.pvm__list {
  flex: 1;
  overflow-y: auto;
  padding: 6px 0;
  scrollbar-width: thin;
  scrollbar-color: var(--color-border) transparent;
}

.pvm__row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 16px;
  transition: background 0.08s;
}

.pvm__row:hover {
  background: var(--color-hover);
}

.pvm__type-icon {
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.pvm__name {
  font-size: 0.85rem;
  color: var(--color-text);
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pvm__mode-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  background: transparent;
  cursor: pointer;
  font-size: 0.75rem;
  color: var(--color-text-muted);
  transition: background 0.1s, color 0.1s, border-color 0.1s;
  flex-shrink: 0;
}

.pvm__mode-btn:hover {
  background: var(--color-hover);
}

.pvm__mode-btn--show {
  color: var(--color-accent);
  border-color: var(--color-accent);
}

.pvm__mode-btn--hide_empty {
  color: #c8a820;
  border-color: #c8a820;
}

.pvm__mode-btn--hide {
  color: var(--color-text-muted);
  border-color: var(--color-border);
  opacity: 0.6;
}

.pvm__mode-label {
  white-space: nowrap;
}

/* ── Footer ─────────────────────────────────────────────────────────────── */

.pvm__footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 10px 16px;
  border-top: 1px solid var(--color-border);
}

.pvm__cancel-btn {
  padding: 6px 14px;
  border: 1px solid var(--color-border);
  border-radius: 5px;
  background: transparent;
  cursor: pointer;
  font-size: 0.8rem;
  color: var(--color-text-muted);
  transition: background 0.1s;
}

.pvm__cancel-btn:hover {
  background: var(--color-hover);
}

.pvm__save-btn {
  padding: 6px 14px;
  border: none;
  border-radius: 5px;
  background: var(--color-accent);
  color: #fff;
  cursor: pointer;
  font-size: 0.8rem;
  font-weight: 600;
  transition: opacity 0.12s;
}

.pvm__save-btn:hover {
  opacity: 0.85;
}
</style>

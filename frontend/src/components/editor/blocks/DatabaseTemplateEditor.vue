<script setup lang="ts">
/**
 * DatabaseTemplateEditor
 *
 * Slide-in panel for editing a single entry template.
 *
 * Structure
 * ---------
 * Reuses the same editing stack as SideView (BlockTopSection,
 * BlockPropertySection, BlockContentSection) but wraps it in a narrower
 * overlay with a "Template" badge in the header instead of entry navigation.
 *
 * Template values are loaded from the databaseTemplates store, which fetches
 * them from GET /{database_id}/entry-templates. Property mutations go through
 * dbStore.upsertValue (same endpoint as regular entries — the backend accepts
 * entry_template blocks identically). After every refresh the template list is
 * re-fetched so the values stay in sync.
 *
 * Props
 * -----
 * databaseId  – the database that owns the template.
 * templateId  – the entry_template block ID to edit.
 *
 * Emits
 * -----
 * close       – close the editor.
 */
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { useBlockStore, type Block } from '@/stores/blocks'
import { useDatabaseStore, type DatabaseEntry } from '@/stores/database'
import { useDatabaseTemplatesStore } from '@/stores/databaseTemplates'
import { WS_BLOCK_EVENT, type BlockEventPayload } from '@/stores/ws'
import BlockTopSection from '@/components/main/BlockTopSection.vue'
import BlockPropertySection from '@/components/main/BlockPropertySection.vue'
import BlockContentSection from '@/components/main/BlockContentSection.vue'

// ── Props / emits ─────────────────────────────────────────────────────────────

const props = defineProps<{
  databaseId: string
  templateId: string
}>()

const emit = defineEmits<{
  (e: 'close'): void
}>()

// ── Dependencies ──────────────────────────────────────────────────────────────

const { t } = useI18n()
const blockStore = useBlockStore()
const dbStore = useDatabaseStore()
const templateStore = useDatabaseTemplatesStore()

// ── Load ──────────────────────────────────────────────────────────────────────

const loading = ref(true)
const error = ref(false)

const block = computed<Block | null>(() =>
  blockStore.blocks[props.templateId] ?? null,
)

/**
 * Build a DatabaseEntry-compatible shape for BlockPropertySection.
 *
 * - content / icon come from the reactive blockStore so that title and icon
 *   changes made via BlockTopSection are reflected immediately (the blockStore
 *   patches blocks[templateId] in-place on every WS event).
 * - values come from the templateStore, which is refreshed after every
 *   property mutation (onRefresh) and on every relevant WS event (_onWsEvent).
 */
const templateEntry = computed<DatabaseEntry | null>(() => {
  if (!block.value) return null
  const tmpl = templateStore.getTemplates(props.databaseId)
    .find((t) => t.id === props.templateId)
  return {
    id: props.templateId,
    position: block.value.position,
    content: block.value.content ?? null,
    icon: block.value.icon ?? null,
    state: block.value.state,
    values: (tmpl?.values ?? {}) as DatabaseEntry['values'],
  }
})

async function load(): Promise<void> {
  loading.value = true
  error.value = false
  try {
    await blockStore.fetchBlock(props.templateId)
    await blockStore.fetchChildren(props.templateId)
    await dbStore.fetchSchemas(props.databaseId)
    // Fetch templates so values are populated in the store.
    await templateStore.fetchTemplates(props.databaseId)
  } catch {
    error.value = true
  } finally {
    loading.value = false
  }
}

onMounted(() => load())

// After a property mutation BlockPropertySection emits 'refresh'.
// Re-fetch templates so values stay in sync.
async function onRefresh(): Promise<void> {
  await templateStore.fetchTemplates(props.databaseId)
}

// ── Slide-in animation ────────────────────────────────────────────────────────

const visible = ref(false)

onMounted(() => {
  nextTick(() => { visible.value = true })
})

function close(): void {
  visible.value = false
  setTimeout(() => emit('close'), 220)
}

// ── Keyboard ──────────────────────────────────────────────────────────────────

function onKeydown(e: KeyboardEvent): void {
  if (e.key === 'Escape') close()
}

// ── WebSocket sync ────────────────────────────────────────────────────────────

/**
 * Re-fetch template values whenever the backend broadcasts an update for this
 * template block. This covers:
 * - content_updated  (title changed via BlockTopSection)
 * - appearance_updated (icon changed)
 * - database_entries_updated on the owning database (value upsert from another session)
 *
 * The blockStore already patches blocks[templateId] in-place for block-level
 * events, so BlockTopSection stays reactive automatically. We only need to
 * re-fetch the templateStore for value changes.
 */
function _onWsEvent(e: Event): void {
  const { event_type, block_id } = (e as CustomEvent<BlockEventPayload>).detail
  // Value mutations broadcast database_entries_updated with block_id = databaseId.
  if (event_type === 'database_entries_updated' && block_id === props.databaseId) {
    templateStore.fetchTemplates(props.databaseId)
    return
  }
  // Direct block updates on the template itself (title, icon).
  if (
    (event_type === 'content_updated' || event_type === 'appearance_updated') &&
    block_id === props.templateId
  ) {
    templateStore.fetchTemplates(props.databaseId)
  }
}

onMounted(() => {
  document.addEventListener('keydown', onKeydown)
  window.addEventListener(WS_BLOCK_EVENT, _onWsEvent)
})

onUnmounted(() => {
  document.removeEventListener('keydown', onKeydown)
  window.removeEventListener(WS_BLOCK_EVENT, _onWsEvent)
})

// ── Panel resize ──────────────────────────────────────────────────────────────

const STORAGE_KEY = 'dte-panel-width'
const MIN_WIDTH = 280
const MAX_WIDTH_RATIO = 0.92

function clampWidth(w: number): number {
  return Math.min(Math.max(w, MIN_WIDTH), Math.floor(window.innerWidth * MAX_WIDTH_RATIO))
}

function readStoredWidth(): number {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored) {
      const n = parseInt(stored, 10)
      if (!isNaN(n)) return clampWidth(n)
    }
  } catch { /* storage unavailable */ }
  return clampWidth(Math.min(Math.max(Math.round(window.innerWidth * 0.5), 380), 640))
}

const panelWidth = ref(380)
const isResizing = ref(false)

onMounted(() => { panelWidth.value = readStoredWidth() })

function onResizeStart(e: MouseEvent): void {
  e.preventDefault()
  isResizing.value = true
  document.addEventListener('mousemove', onResizeMove)
  document.addEventListener('mouseup', onResizeEnd)
  document.body.style.userSelect = 'none'
  document.body.style.cursor = 'col-resize'
}

function onResizeMove(e: MouseEvent): void {
  panelWidth.value = clampWidth(window.innerWidth - e.clientX)
}

function onResizeEnd(): void {
  isResizing.value = false
  document.removeEventListener('mousemove', onResizeMove)
  document.removeEventListener('mouseup', onResizeEnd)
  document.body.style.userSelect = ''
  document.body.style.cursor = ''
  try {
    localStorage.setItem(STORAGE_KEY, String(panelWidth.value))
  } catch { /* storage unavailable */ }
}

onUnmounted(() => {
  document.removeEventListener('mousemove', onResizeMove)
  document.removeEventListener('mouseup', onResizeEnd)
  document.body.style.userSelect = ''
  document.body.style.cursor = ''
})

// ── Property section visibility ───────────────────────────────────────────────

const showProperties = ref(true)
</script>

<template>
  <Teleport to="body">
    <div
      class="dte-panel"
      :class="{ 'dte-panel--open': visible, 'dte-panel--resizing': isResizing }"
      :style="{ width: panelWidth + 'px' }"
      role="dialog"
      :aria-label="t('db.templates.editorTitle')"
    >
      <!-- Resize handle -->
      <div class="dte-resize-handle" @mousedown="onResizeStart" />

      <!-- Header bar -->
      <div class="dte__header">
        <span class="dte__badge">
          <Icon icon="mdi:file-document-edit-outline" width="13" height="13" />
          {{ t('db.templates.editorBadge') }}
        </span>
        <div class="dte__header-spacer" />
        <button
          class="dte__header-btn"
          :title="t('actions.cancel')"
          @click="close"
        >
          <Icon icon="mdi:close" width="15" height="15" />
        </button>
      </div>

      <!-- Loading / error states -->
      <div v-if="loading" class="dte__state" aria-busy="true">
        <span class="dte__spinner" />
      </div>

      <div v-else-if="error" class="dte__state">
        {{ t('errors.loadFailed') }}
      </div>

      <!-- Template editing body -->
      <div v-else-if="block && templateEntry" class="dte__body">
        <BlockTopSection :block="block" />

        <!-- Property section toggle -->
        <button
          class="dte__props-toggle"
          :title="showProperties ? t('propertySection.hideSection') : t('propertySection.showSection')"
          @click="showProperties = !showProperties"
        >
          <Icon
            :icon="showProperties ? 'mdi:chevron-down' : 'mdi:chevron-right'"
            width="14"
            height="14"
          />
          <span>{{ t('propertySection.title') }}</span>
        </button>

        <BlockPropertySection
          v-if="showProperties"
          :database-id="databaseId"
          :entry="templateEntry"
          @refresh="onRefresh"
        />

        <BlockContentSection
          :parent-id="templateId"
          :database-id="databaseId"
        />
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
/* ── Panel ──────────────────────────────────────────────────────────────── */

.dte-panel {
  position: fixed;
  top: 0;
  right: 0;
  height: 100%;
  background: var(--color-bg);
  border-left: 1px solid var(--color-border);
  box-shadow: -4px 0 24px rgba(0, 0, 0, 0.10);
  display: flex;
  flex-direction: column;
  transform: translateX(100%);
  transition: transform 0.22s cubic-bezier(0.4, 0, 0.2, 1);
  overflow: hidden;
  z-index: 200;
}

.dte-panel--open {
  transform: translateX(0);
}

.dte-panel--resizing {
  transition: none;
}

/* ── Resize handle ───────────────────────────────────────────────────────── */

.dte-resize-handle {
  position: absolute;
  top: 0;
  left: 0;
  width: 5px;
  height: 100%;
  cursor: col-resize;
  z-index: 1;
}

.dte-resize-handle:hover {
  background: var(--color-accent-subtle);
}

/* ── Header ─────────────────────────────────────────────────────────────── */

.dte__header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 10px;
  height: 42px;
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.dte__badge {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--color-text-muted);
  background: var(--color-hover);
  padding: 2px 8px;
  border-radius: 4px;
  user-select: none;
}

.dte__header-spacer {
  flex: 1;
}

.dte__header-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 5px;
  background: none;
  cursor: pointer;
  color: var(--color-text-muted);
  transition: background 0.12s, color 0.12s;
}

.dte__header-btn:hover {
  background: var(--color-hover);
  color: var(--color-text);
}

/* ── Loading / error states ─────────────────────────────────────────────── */

.dte__state {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.dte__spinner {
  width: 20px;
  height: 20px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-accent);
  border-radius: 50%;
  animation: dte-spin 0.7s linear infinite;
}

@keyframes dte-spin {
  to { transform: rotate(360deg); }
}

/* ── Body ───────────────────────────────────────────────────────────────── */

.dte__body {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}

/* ── Property section toggle (mirrors SideView style) ───────────────────── */

.dte__props-toggle {
  display: flex;
  align-items: center;
  gap: 5px;
  width: 100%;
  padding: 4px 12px;
  border: none;
  background: none;
  cursor: pointer;
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--color-text-muted);
  text-align: left;
  transition: color 0.1s;
}

.dte__props-toggle:hover {
  color: var(--color-text);
}
</style>

<script setup lang="ts">
/**
 * SideView
 *
 * Slide-in panel rendered on the right side of DatabaseBlock when opening
 * a database entry.  Replaces the previous behaviour of navigating away
 * via router.push.
 *
 * Composition
 * -----------
 * BlockTopSection        – icon, cover, title
 * BlockPropertySection   – grouped property editor (collapsible, default open)
 * BlockCommentSection    – flat comment list (collapsible, default open)
 * BlockContentSection    – child block content
 *
 * The panel is fixed to the right edge of the viewport, slides in with a CSS
 * transition, and can be closed via the "x" button or Escape.  There is no
 * backdrop: the main view remains fully interactive while the panel is open.
 *
 * The panel width is user-resizable via a drag handle on its left edge.
 * The chosen width is persisted in localStorage under the key
 * "sv-panel-width" and restored on the next open.
 *
 * "Open as page" navigates to the full-page entry view with ?showProperties=1
 * so the property section is expanded there by default.
 */
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { useBlockStore, type Block } from '@/stores/blocks'
import { useDatabaseStore, type DatabaseEntry } from '@/stores/database'
import { WS_BLOCK_EVENT, type BlockEventPayload } from '@/stores/ws'
import { useEscapeKey } from '@/composables/useEscapeStack'
import BlockTopSection from './BlockTopSection.vue'
import BlockPropertySection from './BlockPropertySection.vue'
import BlockContentSection from './BlockContentSection.vue'
import BlockCommentSection from './BlockCommentSection.vue'

// ── Props / emits ─────────────────────────────────────────────────────────────

const props = defineProps<{
  /** The database block ID that owns this entry. */
  databaseId: string
  /** The entry (page-type child block) ID to display. */
  entryId: string
}>()

const emit = defineEmits<{
  (e: 'close'): void
  /** Emitted after a relation or other mutation that should refresh the table. */
  (e: 'refresh'): void
}>()

// ── Dependencies ──────────────────────────────────────────────────────────────

const { t } = useI18n()
const router = useRouter()
const blockStore = useBlockStore()
const dbStore = useDatabaseStore()

// ── Load entry block ──────────────────────────────────────────────────────────

const loading = ref(true)
const error = ref(false)

const block = computed<Block | null>(() =>
  blockStore.blocks[props.entryId] ?? null,
)

const entry = computed<DatabaseEntry | null>(() => {
  const entries = dbStore.getEntries(props.databaseId)
  return entries.find((e) => e.id === props.entryId) ?? null
})

async function loadEntry(): Promise<void> {
  loading.value = true
  error.value = false
  try {
    await blockStore.fetchBlock(props.entryId)
    await blockStore.fetchChildren(props.entryId)
    // Ensure schemas and entries are loaded for the property section.
    await dbStore.fetchSchemas(props.databaseId)
    if (!entry.value) {
      await dbStore.fetchEntries(props.databaseId)
    }
  } catch {
    error.value = true
  } finally {
    loading.value = false
  }
}

onMounted(() => loadEntry())

watch(() => props.entryId, () => loadEntry())

// ── WebSocket sync ────────────────────────────────────────────────────────────

/**
 * Re-fetch this entry's data whenever the backend broadcasts an update for the
 * owning database. Value mutations broadcast database_entries_updated with
 * block_id = databaseId; this covers, in particular, a bilateral relation whose
 * mirror value was written on this entry server-side (from this or another
 * session) while the panel is open.
 *
 * Block-level events (title, icon) are already patched in-place in the
 * blockStore, and ``block`` is a computed over blockStore.blocks[entryId], so
 * those stay reactive without an explicit re-fetch here.
 */
function _onWsEvent(e: Event): void {
  const { event_type, block_id } = (e as CustomEvent<BlockEventPayload>).detail
  if (event_type === 'database_entries_updated' && block_id === props.databaseId) {
    dbStore.fetchEntries(props.databaseId)
  }
}

onMounted(() => window.addEventListener(WS_BLOCK_EVENT, _onWsEvent))
onUnmounted(() => window.removeEventListener(WS_BLOCK_EVENT, _onWsEvent))

// ── Slide-in animation ────────────────────────────────────────────────────────

const visible = ref(false)

onMounted(() => {
  nextTick(() => { visible.value = true })
})

function close(): void {
  visible.value = false
  // Wait for CSS transition to finish before unmounting.
  setTimeout(() => emit('close'), 220)
}

// ── Keyboard ──────────────────────────────────────────────────────────────────

// Close on Escape via the shared overlay stack. Any overlay opened above this
// panel (e.g. a select, multi-select or relation picker) registers on top of
// the stack and intercepts Escape first, so a single press dismisses only the
// top-most overlay instead of collapsing the whole panel. See
// composables/useEscapeStack.
useEscapeKey(close)

// ── Open as full page ─────────────────────────────────────────────────────────

// Pass ?showProperties=1 so MainView mirrors the current expanded state.
function openAsPage(): void {
  emit('close')
  const query = showProperties.value ? '?showProperties=1' : ''
  router.push(`/blocks/${props.entryId}${query}`)
}

// ── Refresh passthrough ───────────────────────────────────────────────────────

function onRefresh(): void {
  emit('refresh')
}

// ── Property section visibility ───────────────────────────────────────────────

/** Whether BlockPropertySection is currently expanded. Default: open. */
const showProperties = ref(true)

/** Whether BlockCommentSection is currently expanded. Default: open. */
const showComments = ref(true)

// ── Panel resize ──────────────────────────────────────────────────────────────

const STORAGE_KEY = 'sv-panel-width'
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
  // Default: mirrors the previous clamp(380px, 50vw, 640px) behaviour.
  return clampWidth(Math.min(Math.max(Math.round(window.innerWidth * 0.5), 380), 640))
}

const panelWidth = ref(380)
const isResizing = ref(false)

onMounted(() => {
  panelWidth.value = readStoredWidth()
})

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
  // Clean up in case the component is destroyed mid-drag.
  document.removeEventListener('mousemove', onResizeMove)
  document.removeEventListener('mouseup', onResizeEnd)
  document.body.style.userSelect = ''
  document.body.style.cursor = ''
})
</script>

<template>
  <Teleport to="body">
    <div
      class="sv-panel"
      :class="{ 'sv-panel--open': visible, 'sv-panel--resizing': isResizing }"
      :style="{ width: panelWidth + 'px' }"
      role="dialog"
      :aria-label="t('sideView.title')"
    >
      <!-- Resize handle -->
      <div class="sv-resize-handle" @mousedown="onResizeStart" />

      <!-- Header bar -->
      <div class="sv__header">
        <button
          class="sv__header-btn"
          :title="t('sideView.openAsPage')"
          @click="openAsPage"
        >
          <Icon icon="mdi:arrow-expand" width="15" height="15" />
        </button>
        <div class="sv__header-spacer" />
        <button
          class="sv__header-btn"
          :title="t('actions.cancel')"
          @click="close"
        >
          <Icon icon="mdi:close" width="15" height="15" />
        </button>
      </div>

      <!-- Content -->
      <div v-if="loading" class="sv__state" aria-busy="true">
        <span class="sv__spinner" />
      </div>

      <div v-else-if="error" class="sv__state">
        {{ t('errors.loadFailed') }}
      </div>

      <div v-else-if="block && entry" class="sv__body">
        <BlockTopSection :block="block" />

        <!-- Property section toggle -->
        <button
          class="sv__props-toggle"
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
          :entry="entry"
          @refresh="onRefresh"
        />

        <!-- Comment section toggle -->
        <button
          class="sv__comments-toggle"
          :title="showComments ? t('commentSection.hideSection') : t('commentSection.showSection')"
          @click="showComments = !showComments"
        >
          <Icon
            :icon="showComments ? 'mdi:chevron-down' : 'mdi:chevron-right'"
            width="14"
            height="14"
          />
          <Icon icon="mdi:comment-text-multiple-outline" width="14" height="14" class="sv__section-icon" />
          <span>{{ t('commentSection.title') }}</span>
        </button>

        <BlockCommentSection
          v-if="showComments"
          :block-id="entryId"
        />

        <BlockContentSection :parent-id="entryId" :database-id="databaseId" />
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
/* ── Panel ──────────────────────────────────────────────────────────────── */

.sv-panel {
  position: fixed;
  top: 0;
  right: 0;
  height: 100%;
  /* width is controlled inline via panelWidth */
  background: var(--color-bg);
  border-left: 1px solid var(--color-border);
  box-shadow: -4px 0 24px rgba(0, 0, 0, 0.10);
  display: flex;
  flex-direction: column;
  transform: translateX(100%);
  transition: transform 0.22s cubic-bezier(0.4, 0, 0.2, 1);
  overflow: hidden;
  z-index: 900;
}

.sv-panel--open {
  transform: translateX(0);
}

/* Suppress the slide transition while the user is actively dragging the
   handle, so the panel edge tracks the cursor without lag. */
.sv-panel--resizing {
  transition: none;
}

/* ── Resize handle ──────────────────────────────────────────────────────── */

.sv-resize-handle {
  position: absolute;
  top: 0;
  left: 0;
  width: 6px;
  height: 100%;
  cursor: col-resize;
  z-index: 1;
  /* Transparent by default; accent stripe appears on hover / active drag. */
}

.sv-resize-handle::after {
  content: '';
  position: absolute;
  top: 0;
  left: 2px;
  width: 2px;
  height: 100%;
  background: var(--color-accent);
  opacity: 0;
  transition: opacity 0.15s;
}

.sv-resize-handle:hover::after,
.sv-panel--resizing .sv-resize-handle::after {
  opacity: 0.5;
}

/* ── Header ─────────────────────────────────────────────────────────────── */

.sv__header {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 10px;
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.sv__header-spacer {
  flex: 1;
}

.sv__header-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 6px;
  background: transparent;
  cursor: pointer;
  color: var(--color-text-muted);
  transition: background 0.12s, color 0.12s;
}

.sv__header-btn:hover {
  background: var(--color-hover);
  color: var(--color-text);
}

/* ── Property section toggle ─────────────────────────────────────────────── */

.sv__props-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 7px 16px;
  border: none;
  border-top: 1px solid var(--color-border);
  background: none;
  cursor: pointer;
  color: var(--color-text-muted);
  font-size: 0.8125rem;
  text-align: left;
  flex-shrink: 0;
  transition: color 0.12s, background 0.12s;
}

.sv__props-toggle:hover {
  color: var(--color-text);
  background: var(--color-hover);
}

/* ── Comment section toggle ──────────────────────────────────────────────── */

.sv__comments-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 7px 16px;
  border: none;
  border-top: 1px solid var(--color-border);
  background: none;
  cursor: pointer;
  color: var(--color-text-muted);
  font-size: 0.8125rem;
  text-align: left;
  flex-shrink: 0;
  transition: color 0.12s, background 0.12s;
}

.sv__comments-toggle:hover {
  color: var(--color-text);
  background: var(--color-hover);
}

.sv__section-icon {
  opacity: 0.6;
  flex-shrink: 0;
}

/* ── Body ───────────────────────────────────────────────────────────────── */

.sv__body {
  flex: 1;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: var(--color-border) transparent;
}

/* ── Loading / error states ─────────────────────────────────────────────── */

.sv__state {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 1;
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.sv__spinner {
  display: inline-block;
  width: 20px;
  height: 20px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-accent);
  border-radius: 50%;
  animation: sv-spin 0.7s linear infinite;
}

@keyframes sv-spin {
  to { transform: rotate(360deg); }
}
</style>

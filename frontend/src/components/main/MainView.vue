<script setup lang="ts">
/**
 * MainView
 *
 * Top-level content area. Renders the currently active block (page or
 * database) and exposes per-page controls in the top-right corner.
 *
 * Changes:
 * - Page settings button (top-right, sticky) opens PageSettingsModal.
 * - Full-size mode: removes the max-width cap from BlockTopSection and
 *   BlockContentSection via a CSS class on the wrapper; persisted per page
 *   via the backend preferences API (GET/PUT /api/blocks/{id}/preferences/full-size)
 *   so the setting is consistent across devices.
 * - Cover management is delegated to PageSettingsModal.
 * - Comment section: rendered beneath BlockPropertySection (or beneath
 *   BlockTopSection for non-entry pages). Collapsible, default hidden,
 *   matching the PropertySection default. Available on all block types.
 */
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import BlockTopSection from './BlockTopSection.vue'
import BlockContentSection from './BlockContentSection.vue'
import BlockPropertySection from './BlockPropertySection.vue'
import BlockCommentSection from './BlockCommentSection.vue'
import DatabaseBlock from '@/components/editor/blocks/DatabaseBlock.vue'
import PageSettingsModal from './PageSettingsModal.vue'
import { useBlockStore } from '@/stores/blocks'
import { useDatabaseStore, type DatabaseEntry } from '@/stores/database'

const props = defineProps<{
  blockId?: string | null
}>()

const { t } = useI18n()
const route = useRoute()
const blockStore = useBlockStore()
const dbStore = useDatabaseStore()
const error = ref(false)
const loading = ref(false)

// Read the block reactively from the store so that any in-place update
// (e.g. icon/cover change) is reflected immediately without a reload.
const block = computed(() =>
  props.blockId ? blockStore.blocks[props.blockId] ?? null : null,
)

async function load(id: string | null | undefined): Promise<void> {
  if (!id) return
  loading.value = true
  error.value = false
  showProperties.value = false
  showComments.value = false
  try {
    await blockStore.fetchBlock(id)
    // Fetch parent block to determine whether this block is a database entry.
    // Guard against unnecessary network round-trips when already cached.
    const b = blockStore.blocks[id]
    if (b?.parent_id && !blockStore.blocks[b.parent_id]) {
      await blockStore.fetchBlock(b.parent_id)
    }
    await blockStore.fetchChildren(id)
    // Honor ?showProperties=1 passed from SideView.openAsPage().
    if (isEntry.value && route.query.showProperties === '1') {
      showProperties.value = true
    }
  } catch {
    error.value = true
  } finally {
    loading.value = false
  }
}

onMounted(() => load(props.blockId))
watch(() => props.blockId, (id) => load(id))

// ── Database-entry awareness ──────────────────────────────────────────────────

const parentBlock = computed(() =>
  block.value?.parent_id ? blockStore.blocks[block.value.parent_id] ?? null : null,
)

/** True when the currently viewed block is an entry inside a database. */
const isEntry = computed(() => parentBlock.value?.type === 'database')

/** The database block ID that owns this entry, or null for plain pages. */
const entryDatabaseId = computed<string | null>(() =>
  isEntry.value ? (block.value?.parent_id ?? null) : null,
)

const entry = computed<DatabaseEntry | null>(() => {
  if (!entryDatabaseId.value || !props.blockId) return null
  return dbStore.getEntries(entryDatabaseId.value).find(e => e.id === props.blockId) ?? null
})

// ── Property section visibility ───────────────────────────────────────────────

/** Whether BlockPropertySection is currently expanded. Local state only. */
const showProperties = ref(false)

// Load schemas and entries on demand — only when the section is first opened.
watch(showProperties, async (show) => {
  if (!show || !entryDatabaseId.value) return
  await dbStore.fetchSchemas(entryDatabaseId.value)
  if (!entry.value) {
    await dbStore.fetchEntries(entryDatabaseId.value)
  }
})

/** Whether BlockCommentSection is currently expanded. Local state only. */
const showComments = ref(false)

// ── Page settings modal ───────────────────────────────────────────────────────

const showPageSettings = ref(false)

// ── Full-size mode ────────────────────────────────────────────────────────────

// Persisted per page via the backend preferences API so the setting is
// device-independent. Key: "full-size". Falls back to false on any error
// (network unavailable, 404 = not yet set).

const PREF_KEY = 'full-size'

async function fetchFullSize(id: string): Promise<boolean> {
  try {
    const res = await fetch(`/api/blocks/${id}/preferences/${PREF_KEY}`)
    if (!res.ok) return false
    const data = await res.json()
    return data.value === true
  } catch {
    return false
  }
}

async function saveFullSize(id: string, val: boolean): Promise<void> {
  try {
    await fetch(`/api/blocks/${id}/preferences/${PREF_KEY}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value: val }),
    })
  } catch {
    // Best-effort: UI state is already updated; a failed write is non-critical.
  }
}

const fullSize = ref(false)

// Load preference whenever the active page changes.
watch(
  () => props.blockId,
  async (id) => {
    fullSize.value = id ? await fetchFullSize(id) : false
  },
  { immediate: true },
)

async function onFullSizeUpdate(val: boolean): Promise<void> {
  fullSize.value = val
  if (!props.blockId) return
  await saveFullSize(props.blockId, val)
}
</script>

<template>
  <div
    class="main-view"
    :class="{ 'main-view--full-size': fullSize }"
  >
    <div v-if="loading" class="main-view__state" aria-busy="true">
      <span class="spinner" />
    </div>

    <div v-else-if="error" class="main-view__state">
      {{ t('errors.loadFailed') }}
    </div>

    <div v-else-if="!blockId" class="main-view__state">
      <span>{{ t('main.noPageSelected') }}</span>
    </div>

    <template v-else-if="block">
      <!-- Sticky toolbar: page settings button.
           Shown for all block types (pages and databases) so that
           permissions can be managed from any full-screen view. -->
      <div class="main-view__toolbar">
        <button
          class="main-view__settings-btn"
          :title="t('pageSettings.title')"
          @click="showPageSettings = true"
        >
          <Icon icon="mdi:cog-outline" width="15" height="15" />
        </button>
      </div>

      <DatabaseBlock v-if="block.type === 'database'" :block-id="block.id" />

      <template v-else>
        <BlockTopSection :block="block" />

        <!-- Property section toggle — only visible for database entries. -->
        <template v-if="isEntry">
          <button
            class="main-view__props-toggle"
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
            v-if="showProperties && entry && entryDatabaseId"
            :database-id="entryDatabaseId"
            :entry="entry"
          />
        </template>

        <button
          class="main-view__comments-toggle"
          :title="showComments ? t('commentSection.hideSection') : t('commentSection.showSection')"
          @click="showComments = !showComments"
        >
          <Icon
            :icon="showComments ? 'mdi:chevron-down' : 'mdi:chevron-right'"
            width="14"
            height="14"
          />
          <Icon icon="mdi:comment-text-multiple-outline" width="14" height="14" class="main-view__section-icon" />
          <span>{{ t('commentSection.title') }}</span>
        </button>

        <BlockCommentSection
          v-if="showComments"
          :block-id="block.id"
        />

        <BlockContentSection :parent-id="block.id" />
      </template>
    </template>

    <!-- Page / block settings modal (pages and databases) -->
    <PageSettingsModal
      v-if="showPageSettings && block"
      :block="block"
      :full-size="fullSize"
      @close="showPageSettings = false"
      @update:full-size="onFullSizeUpdate"
    />
  </div>
</template>

<style scoped>
.main-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: var(--color-border) transparent;
  position: relative;
}

/* ── Full-size mode ──────────────────────────────────────────────────────── */
/*
 * Override the max-width constraint that BlockTopSection (.block-meta) and
 * BlockContentSection (.block-content) apply by default. Using :deep() on
 * the parent is the standard Vue scoped-CSS pattern for cross-component
 * overrides without making the child components aware of the fullSize state.
 */
.main-view--full-size :deep(.block-meta) {
  max-width: none;
}

.main-view--full-size :deep(.block-cover) {
  max-width: none;
}

.main-view--full-size :deep(.block-content) {
  max-width: none;
}

.main-view--full-size :deep(.bps) {
  max-width: none;
  padding-left: 0;
  padding-right: 0;
}

/* ── Toolbar (settings button row) ───────────────────────────────────────── */
.main-view__toolbar {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  justify-content: flex-end;
  padding: 6px 10px;
  /* Transparent so it doesn't obscure the cover image or title area.
     The button itself has a subtle hover background. */
  pointer-events: none;
}

.main-view__settings-btn {
  pointer-events: all;
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

.main-view__settings-btn:hover {
  background: var(--color-hover);
  color: var(--color-text);
}

/* ── Property section toggle ─────────────────────────────────────────────── */

.main-view__props-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 7px 2rem;
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

.main-view__props-toggle:hover {
  color: var(--color-text);
  background: var(--color-hover);
}

/* ── Comment section toggle ──────────────────────────────────────────────── */

.main-view__comments-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 7px 2rem;
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

.main-view__comments-toggle:hover {
  color: var(--color-text);
  background: var(--color-hover);
}

.main-view__section-icon {
  opacity: 0.6;
  flex-shrink: 0;
}

/* ── Generic states ──────────────────────────────────────────────────────── */
.main-view__state {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 1;
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.spinner {
  display: inline-block;
  width: 20px;
  height: 20px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-accent);
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* ── Print ───────────────────────────────────────────────────────────────── */
@media print {
  .main-view {
    overflow: visible !important;
    height: auto !important;
    display: block !important;
  }

  .main-view__toolbar {
    display: none !important;
  }

  .main-view__props-toggle {
    display: none !important;
  }

  .main-view__comments-toggle {
    display: none !important;
  }

  /* Force full-width regardless of full-size setting */
  .main-view :deep(.block-meta),
  .main-view :deep(.block-cover),
  .main-view :deep(.block-content) {
    max-width: none !important;
  }
}
</style>

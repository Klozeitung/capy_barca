<script setup lang="ts">
/**
 * RelationCell
 *
 * Renders and edits a ``relation``-type property value.
 *
 * Value shape: ``{ related_ids: string[] }`` or ``null``.
 *
 * The component fetches entries of the target database on every picker open.
 * If entries are already cached they are shown immediately while a silent
 * background refresh runs; an explicit loading spinner is only shown when no
 * cached entries exist yet.
 *
 * Chip titles, however, are resolved through the store's dedicated
 * ``resolveEntryTitles`` path (POST /entries/resolve-titles), not from the
 * paginated entry cache. This decouples chip rendering from a target
 * database's display limit so a chip renders even when the linked entry sits
 * past the first page of its database (#27).
 *
 * Bilateral sync is performed server-side; the component only needs to emit
 * the updated ``related_ids`` for the current entry.
 *
 * Changes
 * -------
 * #55  Click-away closes the picker: a document-level listener is registered
 *      (via nextTick so the opening click is not caught) whenever isOpen is
 *      true and removed when it becomes false or the component unmounts.
 * #56  openPicker always triggers fetchEntries so that entries are not stale
 *      when multiple views share the same target database or the target DB
 *      has been mutated since the last open. The loading spinner is shown
 *      only when no cached entries exist.
 */
import { ref, computed, onUnmounted, watch, nextTick } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { useDatabaseStore, type PropertySchema, type DatabaseEntry, normalizeSelectOption, optionColorStyle } from '@/stores/database'
import { useBlockStore } from '@/stores/blocks'
import SideView from '@/components/main/SideView.vue'
import TimelineEditor from './TimelineEditor.vue'
import {
  resolveTimelineValue,
  getAllTimelineRelatedIds,
  getTimelineDisplayMode,
  getNuanceConfig,
} from './cellUtils'

// ── Props / emits ─────────────────────────────────────────────────────────────

const props = defineProps<{
  schema: PropertySchema
  entry: DatabaseEntry
  databaseId: string
}>()

const emit = defineEmits<{
  (e: 'change', value: Record<string, unknown> | null): void
}>()

// ── Dependencies ──────────────────────────────────────────────────────────────

const { t } = useI18n()
const dbStore = useDatabaseStore()
const blockStore = useBlockStore()

// ── Timeline ──────────────────────────────────────────────────────────────────

const hasTimeline = computed(() => !!(props.schema.config?.hasTimeline))

// Chip wrapping is opt-in per relation property (#12). Off by default: chips
// stay on a single line and clip within the cell border; on: chips wrap.
const wrapContent = computed(() => props.schema.config?.wrapContent === true)
const timelineOpen = ref(false)
const anchorRect = ref<DOMRect | undefined>()

function openTimeline(event: MouseEvent) {
  anchorRect.value = (event.currentTarget as HTMLElement).getBoundingClientRect()
  timelineOpen.value = true
}

const timelineDisplayMode = computed(() =>
  hasTimeline.value ? getTimelineDisplayMode(props.schema) : 'last'
)

// ── Timeline slot groups (used in "all" display mode) ─────────────────────────

interface SlotGroup {
  key: string
  period: string
  ids: string[]
  nuances: Record<string, string>
}

function _formatPeriodKey(key: string): string {
  if (key === '') return '∞'
  const short = (ts: string) => ts.slice(0, 10)
  if (key.startsWith('→')) return `→ ${short(key.slice(1))}`
  const [s, e] = key.split('→')
  return e ? `${short(s)} → ${short(e)}` : `${short(s)} →`
}

const timelineSlotGroups = computed<SlotGroup[]>(() => {
  if (!hasTimeline.value || timelineDisplayMode.value !== 'all') return []
  const raw = props.entry.values[props.schema.id]
  if (!raw || !('_timeline' in raw)) return []
  const timeline = raw._timeline as Record<string, unknown>
  const keys = Object.keys(timeline)
  const sorted = keys.slice().sort((a, b) => {
    const as_ = a === '' ? '' : (a.startsWith('→') ? '' : a.split('→')[0])
    const bs_ = b === '' ? '' : (b.startsWith('→') ? '' : b.split('→')[0])
    return as_ < bs_ ? -1 : as_ > bs_ ? 1 : 0
  })
  return sorted.map(k => {
    const slot = timeline[k] as Record<string, unknown> | null
    return {
      key: k,
      period: _formatPeriodKey(k),
      ids: (slot?.related_ids as string[] | undefined) ?? [],
      nuances: (slot?.nuances as Record<string, string> | undefined) ?? {},
    }
  })
})

/**
 * Whether a related id should render as a chip.
 *
 * Shown while still pending (no resolve round-trip yet) so the cell is never
 * blank on first render, and once resolved only when the id maps to an active
 * target entry. Ids that resolved to nothing (trashed / foreign) are hidden,
 * replacing the previous "present in the paginated cache" filter that broke
 * past the display limit (#27).
 */
function isRelationVisible(id: string): boolean {
  return !dbStore.isRelationResolved(id) || dbStore.hasRelationEntry(id)
}

/** Filter a set of IDs to those that should render as chips. */
function displayedIdsForGroup(ids: string[]): string[] {
  return ids.filter(isRelationVisible)
}

// ── Derived ───────────────────────────────────────────────────────────────────

const targetDatabaseId = computed<string>(() =>
  (props.schema.config?.target_database_id as string | undefined) ?? props.databaseId,
)

const relatedIds = computed<string[]>(() => {
  const raw = props.entry.values[props.schema.id]
  if (!raw) return []
  if (hasTimeline.value && '_timeline' in raw) {
    const mode = getTimelineDisplayMode(props.schema)
    if (mode === 'all') return getAllTimelineRelatedIds(raw)
    const slot = resolveTimelineValue(raw)
    return (slot?.related_ids as string[] | undefined) ?? []
  }
  return (raw?.related_ids as string[] | undefined) ?? []
})

// ── Nuance ──────────────────────────────────────────────────────────────────
//
// Each relation schema carries its own nuance affixes / orientation in
// ``config.nuance``; the chip is wrapped with an affix-bracketed label per the
// orientation.  When nuance is active the chips stack one per line, matching
// the per-relation line layout.  Absent config → bare chips, as before.

const nuanceCfg = computed(() => getNuanceConfig(props.schema))

/** Nuance map for the "last"/normal mode (resolved last slot, or flat value). */
const currentNuances = computed<Record<string, string>>(() => {
  const raw = props.entry.values[props.schema.id]
  if (!raw) return {}
  const val = hasTimeline.value && '_timeline' in raw ? resolveTimelineValue(raw) : raw
  return (val?.nuances as Record<string, string> | undefined) ?? {}
})

/** True when this id has a non-empty nuance label in the given map. */
function hasNuance(id: string, map: Record<string, string>): boolean {
  return !!nuanceCfg.value && !!(map?.[id])
}

function nuanceLabel(id: string, map: Record<string, string>): string {
  return map?.[id] ?? ''
}

/** Inline style for the nuance label chip, resolved from the shared options. */
function nuanceStyle(label: string): Record<string, string> {
  const opts = (nuanceCfg.value?.options ?? []).map(normalizeSelectOption)
  const opt = opts.find(o => o.label === label)
  return opt?.color ? optionColorStyle(opt.color) : {}
}

const targetEntries = computed(() => dbStore.getEntries(targetDatabaseId.value))

/**
 * IDs rendered as chips in the cell.
 *
 * Visibility is driven by the dedicated title resolver (see
 * ``isRelationVisible``), not the paginated entry cache, so chips render
 * regardless of where the linked entry sits in its target database (#27).
 */
const displayedRelatedIds = computed<string[]>(() =>
  relatedIds.value.filter(isRelationVisible),
)

// Resolve chip titles whenever the linked ids or the target database change.
// Runs immediately so labels are available on first render without loading the
// entire target database; the resolver itself de-duplicates already-known ids.
watch(
  [relatedIds, targetDatabaseId],
  ([ids, dbId]) => {
    if (ids.length > 0) dbStore.resolveEntryTitles(dbId, ids)
  },
  { immediate: true },
)

// ── Picker state ──────────────────────────────────────────────────────────────

const cellEl = ref<HTMLElement | null>(null)
const pickerEl = ref<HTMLElement | null>(null)
const searchInputEl = ref<HTMLInputElement | null>(null)
const pickerStyle = ref<Record<string, string>>({})
const isOpen = ref(false)
const isLoading = ref(false)
const isCreating = ref(false)
const activeIndex = ref(-1)
const searchQuery = ref('')

const filteredEntries = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return targetEntries.value
  return targetEntries.value.filter(e =>
    ((e.content?.title as string | undefined) ?? '').toLowerCase().includes(q),
  )
})

const showCreate = computed(() => {
  const q = searchQuery.value.trim()
  if (!q) return false
  // Show "create" when the query doesn't exactly match any existing entry title.
  return !targetEntries.value.some(
    e => ((e.content?.title as string | undefined) ?? '').toLowerCase() === q.toLowerCase(),
  )
})

watch(filteredEntries, () => { activeIndex.value = -1 })

function itemCount(): number {
  return filteredEntries.value.length + (showCreate.value ? 1 : 0)
}

function onKeyNav(e: KeyboardEvent) {
  const count = itemCount()
  if (count === 0) return
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    activeIndex.value = (activeIndex.value + 1) % count
    scrollActiveIntoView()
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    activeIndex.value = (activeIndex.value - 1 + count) % count
    scrollActiveIntoView()
  }
}

function scrollActiveIntoView() {
  nextTick(() => {
    const el = pickerEl.value?.querySelector<HTMLElement>('[data-nav-active="true"]')
    el?.scrollIntoView({ block: 'nearest' })
  })
}

async function openPicker() {
  if (cellEl.value) {
    const rect = cellEl.value.getBoundingClientRect()
    let top = rect.bottom + 4
    let left = rect.left
    if (left + 280 > window.innerWidth - 8) left = window.innerWidth - 288
    if (top + 280 > window.innerHeight - 8) top = rect.top - 284
    pickerStyle.value = { top: `${top}px`, left: `${left}px` }
  }
  searchQuery.value = ''
  isOpen.value = true

  // #56: Always fetch to stay in sync across views.
  if (targetEntries.value.length === 0) {
    isLoading.value = true
    await dbStore.fetchEntries(targetDatabaseId.value)
    isLoading.value = false
  } else {
    dbStore.fetchEntries(targetDatabaseId.value)
  }

  await nextTick()
  searchInputEl.value?.focus()
}

function closePicker() {
  isOpen.value = false
}

// ── Click-away (#55) ──────────────────────────────────────────────────────────

function onDocumentClick(event: MouseEvent) {
  // Ignore clicks that originate inside the picker (already stopped) or
  // inside the cell element itself (the add-button, tags, etc.).
  const target = event.target as Node | null
  if (pickerEl.value?.contains(target)) return
  if (cellEl.value?.contains(target)) return
  closePicker()
}

watch(isOpen, async (open) => {
  if (open) {
    // nextTick ensures the click that triggered openPicker is not caught
    // immediately and does not close the picker right away.
    await nextTick()
    document.addEventListener('click', onDocumentClick)
  } else {
    document.removeEventListener('click', onDocumentClick)
  }
})

onUnmounted(() => {
  document.removeEventListener('click', onDocumentClick)
})

// ── Helpers ───────────────────────────────────────────────────────────────────

function isSelected(entryId: string): boolean {
  return relatedIds.value.includes(entryId)
}

function getEntryTitle(id: string): string {
  // Prefer the dedicated relation-title resolver (#27); it is independent of
  // the target database's display limit. Fall back to the paginated entry
  // cache (covers freshly created entries before their resolve completes) and
  // finally to the localized "untitled" label.
  const resolved = dbStore.getRelationTitle(id)
  if (resolved) return resolved
  const found = targetEntries.value.find(e => e.id === id)
  return (found?.content?.title as string | undefined) || t('main.untitled')
}

// ── Mutations ─────────────────────────────────────────────────────────────────

/**
 * Emit a flat relation value, preserving per-uid nuances for still-linked
 * entries.  Used only for non-timeline relations; timeline relations are
 * edited through TimelineEditor and keep their pool/_timeline shape.
 */
function emitFlat(ids: string[], nuances: Record<string, string>) {
  if (ids.length === 0) {
    emit('change', null)
    return
  }
  const cleaned: Record<string, string> = {}
  for (const id of ids) {
    if (nuances[id]) cleaned[id] = nuances[id]
  }
  const value: Record<string, unknown> = { related_ids: ids }
  if (Object.keys(cleaned).length > 0) value.nuances = cleaned
  emit('change', value)
}

/** Nuance is editable inline only for flat relations (timeline → TimelineEditor). */
const nuanceEditable = computed(() => !!nuanceCfg.value && !hasTimeline.value)

/** Set or clear the nuance label of a linked entry on a flat relation. */
function setFlatNuance(uid: string, label: string) {
  const ids = [...relatedIds.value]
  const nuances = { ...currentNuances.value }
  if (label) nuances[uid] = label
  else delete nuances[uid]
  emitFlat(ids, nuances)
}

function toggleEntry(entryId: string) {
  const current = [...relatedIds.value]
  const idx = current.indexOf(entryId)
  if (idx === -1) {
    current.push(entryId)
  } else {
    current.splice(idx, 1)
  }
  if (!hasTimeline.value) {
    emitFlat(current, currentNuances.value)
  } else {
    emit('change', current.length > 0 ? { related_ids: current } : null)
  }
}

function removeRelated(entryId: string, event: MouseEvent) {
  event.stopPropagation()
  const updated = relatedIds.value.filter(id => id !== entryId)
  if (!hasTimeline.value) {
    emitFlat(updated, currentNuances.value)
  } else {
    emit('change', updated.length > 0 ? { related_ids: updated } : null)
  }
}

/**
 * Create a new entry in the target database with the search query as its
 * title, link it to the current entry, then clear the search field.
 * Bilateral sync is handled server-side by the existing upsert flow.
 */
async function createAndLink() {
  const title = searchQuery.value.trim()
  if (!title || isCreating.value) return
  isCreating.value = true
  try {
    const newEntry = await dbStore.createEntry(targetDatabaseId.value)
    await blockStore.updateBlock(newEntry.id, { content: { title } })
    await dbStore.fetchEntries(targetDatabaseId.value)
    toggleEntry(newEntry.id)
    searchQuery.value = ''
    await nextTick()
    searchInputEl.value?.focus()
  } finally {
    isCreating.value = false
  }
}

// ── Side view ─────────────────────────────────────────────────────────────────

/** ID of the related entry currently open in the side panel, or null. */
const sideViewEntryId = ref<string | null>(null)

function openRelatedEntry(id: string): void {
  // #14: The clicked chip lives inside cellEl, so the picker's click-away
  // handler ignores it and would leave the picker open behind the side view.
  // Dismiss any open picker / timeline editor explicitly before opening.
  closePicker()
  timelineOpen.value = false
  sideViewEntryId.value = id
}

function closeSideView(): void {
  sideViewEntryId.value = null
}

/** Re-fetch target entries after an edit so chips stay up to date. */
async function onSideViewRefresh(): Promise<void> {
  await dbStore.fetchEntries(targetDatabaseId.value)
  // Force-refresh resolved chip titles so an edited (or trashed) linked entry
  // updates its chip immediately (#27).
  await dbStore.resolveEntryTitles(targetDatabaseId.value, relatedIds.value, true)
}
</script>

<template>
  <div class="rel-cell" ref="cellEl">
    <!-- "all" mode: per-slot grouped display -->
    <template v-if="timelineDisplayMode === 'all' && timelineSlotGroups.length > 0">
      <div class="rel-cell__timeline-list">
        <div
          v-for="group in timelineSlotGroups"
          :key="group.key"
          class="rel-cell__timeline-slot"
        >
          <span class="rel-cell__slot-label">{{ group.period }}</span>
          <div
            class="rel-cell__slot-chips"
            :class="{ 'rel-cell__slot-chips--stack': wrapContent || !!nuanceCfg }"
          >
            <span
              v-for="id in displayedIdsForGroup(group.ids)"
              :key="id"
              class="rel-cell__tag"
              :class="{ 'rel-cell__tag--nuanced': hasNuance(id, group.nuances) }"
            >
              <span
                v-if="hasNuance(id, group.nuances) && nuanceCfg?.orientation === 'prepended'"
                class="rel-cell__nuance"
              >
                <span v-if="nuanceCfg?.affix1" class="rel-cell__affix">{{ nuanceCfg?.affix1 }}</span>
                <span class="rel-cell__nuance-label" :style="nuanceStyle(nuanceLabel(id, group.nuances))">{{ nuanceLabel(id, group.nuances) }}</span>
                <span v-if="nuanceCfg?.affix2" class="rel-cell__affix">{{ nuanceCfg?.affix2 }}</span>
              </span>
              <span
                class="rel-cell__tag-open"
                :title="getEntryTitle(id)"
                @click.stop="openRelatedEntry(id)"
              >
                <Icon icon="mdi:file-outline" width="10" height="10" class="rel-cell__tag-icon" />
                <span class="rel-cell__tag-text">{{ getEntryTitle(id) }}</span>
              </span>
              <span
                v-if="hasNuance(id, group.nuances) && nuanceCfg?.orientation === 'appended'"
                class="rel-cell__nuance"
              >
                <span v-if="nuanceCfg?.affix1" class="rel-cell__affix">{{ nuanceCfg?.affix1 }}</span>
                <span class="rel-cell__nuance-label" :style="nuanceStyle(nuanceLabel(id, group.nuances))">{{ nuanceLabel(id, group.nuances) }}</span>
                <span v-if="nuanceCfg?.affix2" class="rel-cell__affix">{{ nuanceCfg?.affix2 }}</span>
              </span>
            </span>
            <span v-if="group.ids.length === 0" class="rel-cell__slot-empty">—</span>
          </div>
        </div>
        <!-- Clock button to open TimelineEditor -->
        <button
          class="rel-cell__timeline-btn"
          :aria-label="t('db.timeline.title')"
          @click.stop="openTimeline($event)"
        >
          <Icon icon="mdi:clock-outline" width="12" height="12" />
        </button>
      </div>
    </template>

    <!-- "last" / normal mode: flat tag strip -->
    <template v-else>
      <div class="rel-cell__tags" :class="{ 'rel-cell__tags--stack': wrapContent || !!nuanceCfg }">
        <span
          v-for="id in displayedRelatedIds"
          :key="id"
          class="rel-cell__tag"
          :class="{ 'rel-cell__tag--nuanced': nuanceEditable || hasNuance(id, currentNuances) }"
        >
          <span
            v-if="(nuanceEditable || hasNuance(id, currentNuances)) && nuanceCfg?.orientation === 'prepended'"
            class="rel-cell__nuance"
          >
            <span v-if="nuanceCfg?.affix1" class="rel-cell__affix">{{ nuanceCfg?.affix1 }}</span>
            <select
              v-if="nuanceEditable"
              class="rel-cell__nuance-select"
              :value="currentNuances[id] ?? ''"
              @click.stop
              @change="setFlatNuance(id, ($event.target as HTMLSelectElement).value)"
            >
              <option value="">{{ t('db.timeline.nuanceNone') }}</option>
              <option v-for="opt in (nuanceCfg?.options ?? [])" :key="opt.label" :value="opt.label">{{ opt.label }}</option>
            </select>
            <span v-else class="rel-cell__nuance-label" :style="nuanceStyle(nuanceLabel(id, currentNuances))">{{ nuanceLabel(id, currentNuances) }}</span>
            <span v-if="nuanceCfg?.affix2" class="rel-cell__affix">{{ nuanceCfg?.affix2 }}</span>
          </span>
          <span
            class="rel-cell__tag-open"
            :title="getEntryTitle(id)"
            @click.stop="openRelatedEntry(id)"
          >
            <Icon icon="mdi:file-outline" width="10" height="10" class="rel-cell__tag-icon" />
            <span class="rel-cell__tag-text">{{ getEntryTitle(id) }}</span>
          </span>
          <span
            v-if="(nuanceEditable || hasNuance(id, currentNuances)) && nuanceCfg?.orientation === 'appended'"
            class="rel-cell__nuance"
          >
            <span v-if="nuanceCfg?.affix1" class="rel-cell__affix">{{ nuanceCfg?.affix1 }}</span>
            <select
              v-if="nuanceEditable"
              class="rel-cell__nuance-select"
              :value="currentNuances[id] ?? ''"
              @click.stop
              @change="setFlatNuance(id, ($event.target as HTMLSelectElement).value)"
            >
              <option value="">{{ t('db.timeline.nuanceNone') }}</option>
              <option v-for="opt in (nuanceCfg?.options ?? [])" :key="opt.label" :value="opt.label">{{ opt.label }}</option>
            </select>
            <span v-else class="rel-cell__nuance-label" :style="nuanceStyle(nuanceLabel(id, currentNuances))">{{ nuanceLabel(id, currentNuances) }}</span>
            <span v-if="nuanceCfg?.affix2" class="rel-cell__affix">{{ nuanceCfg?.affix2 }}</span>
          </span>
          <button
            class="rel-cell__tag-remove"
            :aria-label="t('db.relation.removeLink')"
            @click="removeRelated(id, $event)"
          >
            <Icon icon="mdi:close" width="10" height="10" />
          </button>
        </span>
        <!-- Timeline mode: clock button instead of picker -->
        <button
          v-if="hasTimeline && !timelineOpen"
          class="rel-cell__timeline-btn"
          :aria-label="t('db.timeline.title')"
          @click.stop="openTimeline($event)"
        >
          <Icon icon="mdi:clock-outline" width="12" height="12" />
        </button>

        <!-- Normal mode: add-link button -->
        <button
          v-else-if="!isOpen && !hasTimeline"
          class="rel-cell__add-btn"
          :aria-label="t('db.relation.addLink')"
          @click.stop="openPicker"
        >
          <Icon icon="mdi:plus" width="13" height="13" />
        </button>
      </div>
    </template>

    <!-- Entry picker -->
    <Teleport to="body">
      <div
        v-if="isOpen"
        ref="pickerEl"
        class="rel-cell__picker"
        :style="pickerStyle"
        @click.stop
        @keydown.esc="closePicker"
      >
        <!-- Search input -->
        <div class="rel-cell__search-wrap">
          <Icon icon="mdi:magnify" width="13" height="13" class="rel-cell__search-icon" />
          <input
            ref="searchInputEl"
            v-model="searchQuery"
            class="rel-cell__search"
            placeholder="Search..."
            @keydown.up.prevent="onKeyNav"
            @keydown.down.prevent="onKeyNav"
            @keydown.enter.prevent="
              activeIndex >= 0
                ? (activeIndex < filteredEntries.length
                    ? toggleEntry(filteredEntries[activeIndex].id)
                    : createAndLink())
                : showCreate
                  ? createAndLink()
                  : undefined
            "
            @click.stop
          />
        </div>

        <div v-if="isLoading" class="rel-cell__picker-loading">
          <Icon icon="mdi:loading" class="rel-cell__spinner" width="14" height="14" />
        </div>
        <template v-else>
          <button
            v-for="(e, i) in filteredEntries"
            :key="e.id"
            class="rel-cell__picker-item"
            :class="{
              'rel-cell__picker-item--selected': isSelected(e.id),
              'rel-cell__picker-item--focused': activeIndex === i,
            }"
            :data-nav-active="activeIndex === i || undefined"
            @click="toggleEntry(e.id)"
            @mouseenter="activeIndex = i"
          >
            <Icon
              :icon="isSelected(e.id) ? 'mdi:checkbox-marked' : 'mdi:checkbox-blank-outline'"
              width="14"
              height="14"
            />
            {{ (e.content?.title as string | undefined) || t('main.untitled') }}
          </button>

          <!-- Empty state: no entries in target DB at all -->
          <div
            v-if="targetEntries.length === 0 && !showCreate"
            class="rel-cell__picker-empty"
          >
            {{ t('db.relation.noEntries') }}
          </div>

          <!-- No search match — offer create & link -->
          <button
            v-if="showCreate"
            class="rel-cell__picker-create"
            :class="{ 'rel-cell__picker-create--focused': activeIndex === filteredEntries.length }"
            :data-nav-active="activeIndex === filteredEntries.length || undefined"
            :disabled="isCreating"
            @click="createAndLink"
            @mouseenter="activeIndex = filteredEntries.length"
          >
            <Icon icon="mdi:plus-circle-outline" width="13" height="13" />
            Create & link "{{ searchQuery.trim() }}"
          </button>
        </template>
        <button class="rel-cell__picker-close" @click="closePicker">
          {{ t('actions.cancel') }}
        </button>
      </div>
    </Teleport>

    <!-- Side panel for the clicked related entry -->
    <SideView
      v-if="sideViewEntryId"
      :database-id="targetDatabaseId"
      :entry-id="sideViewEntryId"
      @close="closeSideView"
      @refresh="onSideViewRefresh"
    />

    <!-- Timeline editor for timeline relations -->
    <TimelineEditor
      v-if="timelineOpen"
      :entry="entry"
      :schema="schema"
      :database-id="databaseId"
      :anchor-rect="anchorRect"
      @close="timelineOpen = false"
    />
  </div>
</template>

<style scoped>
.rel-cell {
  position: relative;
  padding: 4px 8px;
  min-height: 36px;
  display: flex;
  flex-direction: column;
  justify-content: center;
}

/* ── Timeline button ─────────────────────────────────────────────────────── */
.rel-cell__timeline-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  cursor: pointer;
  color: var(--color-text-muted);
  flex-shrink: 0;
  transition: border-color 0.1s, color 0.1s;
}
.rel-cell__timeline-btn:hover {
  border-color: var(--color-accent);
  color: var(--color-accent);
}

/* ── Timeline "all" mode ─────────────────────────────────────────────────── */
.rel-cell__timeline-list {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 4px 8px;
}

.rel-cell__timeline-slot {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  min-height: 20px;
}

.rel-cell__slot-label {
  font-size: 0.68rem;
  color: var(--color-text-muted);
  white-space: nowrap;
  flex-shrink: 0;
  padding-top: 2px;
  min-width: 140px;
}

.rel-cell__slot-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 3px;
  align-items: center;
  flex: 1;
  min-width: 0;
}

/* wrapContent enabled: one chip per line within the slot. */
.rel-cell__slot-chips--stack {
  flex-direction: column;
  flex-wrap: nowrap;
  align-items: flex-start;
}

.rel-cell__slot-empty {
  font-size: 0.72rem;
  color: var(--color-text-muted);
  font-style: italic;
}

/* ── Tags ────────────────────────────────────────────────────────────────── */
/*
 * Default (wrapContent off): chips flow horizontally and wrap onto new lines
 * as needed, within the column width — the column is never stretched, the row
 * grows vertically. wrapContent on: each chip sits on its own line.
 */
.rel-cell__tags {
  display: flex;
  flex-wrap: wrap;
  gap: 3px;
  align-items: center;
}

.rel-cell__tags--stack {
  flex-direction: column;
  flex-wrap: nowrap;
  align-items: flex-start;
}

.rel-cell__tag {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  background: var(--color-accent-subtle);
  border: 1px solid var(--color-accent);
  border-radius: 3px;
  padding: 2px 5px;
  font-size: 0.73rem;
  color: var(--color-text);
  max-width: 130px;
}

.rel-cell__tag-open {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  cursor: pointer;
  flex: 1;
  min-width: 0;
  overflow: hidden;
}

.rel-cell__tag-open:hover .rel-cell__tag-text {
  text-decoration: underline;
}

.rel-cell__tag-icon {
  flex-shrink: 0;
  color: var(--color-text-muted);
}

.rel-cell__tag-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/*
 * Stack mode (wrapContent on): show each chip's full title and let long titles
 * break onto multiple lines inside the chip, instead of the compact single-line
 * ellipsis used in the default flow layout.
 */
.rel-cell__tags--stack .rel-cell__tag,
.rel-cell__slot-chips--stack .rel-cell__tag {
  max-width: 100%;
  align-items: flex-start;
}

.rel-cell__tags--stack .rel-cell__tag-open,
.rel-cell__slot-chips--stack .rel-cell__tag-open {
  overflow: visible;
  align-items: flex-start;
}

.rel-cell__tags--stack .rel-cell__tag-text,
.rel-cell__slot-chips--stack .rel-cell__tag-text {
  white-space: normal;
  overflow: visible;
  text-overflow: clip;
  word-break: break-word;
}

.rel-cell__tag-remove {
  background: transparent;
  border: none;
  cursor: pointer;
  color: var(--color-text-muted);
  display: flex;
  align-items: center;
  padding: 0;
  flex-shrink: 0;
  transition: color 0.15s;
}

.rel-cell__tag-remove:hover {
  color: #e05555;
}

.rel-cell__add-btn {
  background: transparent;
  border: 1px dashed var(--color-border);
  border-radius: 3px;
  cursor: pointer;
  color: var(--color-text-muted);
  display: flex;
  align-items: center;
  padding: 2px 4px;
  flex-shrink: 0;
  transition: color 0.15s, border-color 0.15s;
}

.rel-cell__add-btn:hover {
  color: var(--color-text);
  border-color: var(--color-accent);
}

/* ── Picker ──────────────────────────────────────────────────────────────── */
.rel-cell__picker {
  position: fixed;
  z-index: 1000;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 6px;
  padding: 4px;
  min-width: 200px;
  max-width: 280px;
  max-height: 280px;
  overflow-y: auto;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.2);
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.rel-cell__search-wrap {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 8px;
  border-bottom: 1px solid var(--color-border);
  margin-bottom: 2px;
  flex-shrink: 0;
}

.rel-cell__search-icon {
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.rel-cell__search {
  flex: 1;
  border: none;
  outline: none;
  background: transparent;
  font-size: 0.8rem;
  color: var(--color-text);
  min-width: 0;
}

.rel-cell__search::placeholder {
  color: var(--color-text-muted);
}

.rel-cell__picker-create {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  border-radius: 4px;
  border: none;
  border-top: 1px solid var(--color-border);
  background: transparent;
  cursor: pointer;
  font-size: 0.8rem;
  color: var(--color-accent);
  text-align: left;
  width: 100%;
  margin-top: 2px;
  padding-top: 7px;
  transition: background 0.1s;
  flex-shrink: 0;
}

.rel-cell__picker-create:hover,
.rel-cell__picker-create--focused {
  background: var(--color-hover);
}

.rel-cell__picker-create:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.rel-cell__picker-loading {
  display: flex;
  justify-content: center;
  padding: 12px;
  color: var(--color-text-muted);
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.rel-cell__spinner {
  animation: spin 1s linear infinite;
}

.rel-cell__picker-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.8rem;
  color: var(--color-text);
  background: transparent;
  border: none;
  text-align: left;
  width: 100%;
  transition: background 0.1s;
}

.rel-cell__picker-item:hover,
.rel-cell__picker-item--focused {
  background: var(--color-hover);
}

.rel-cell__picker-item--selected {
  color: var(--color-accent);
}

.rel-cell__picker-empty {
  font-size: 0.8rem;
  color: var(--color-text-muted);
  text-align: center;
  padding: 8px;
  font-style: italic;
}

.rel-cell__picker-close {
  margin-top: 4px;
  padding-top: 6px;
  border: none;
  border-top: 1px solid var(--color-border);
  background: transparent;
  cursor: pointer;
  font-size: 0.75rem;
  color: var(--color-text-muted);
  text-align: center;
  width: 100%;
  transition: color 0.15s;
}

.rel-cell__picker-close:hover {
  color: var(--color-text);
}

/* ── Relation nuance ───────────────────────────────────────────────────────── */
/* Nuanced relations render one per line: chip plus an affix-bracketed label,
 * ordered by the schema's orientation. */
.rel-cell__tag--nuanced {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.rel-cell__nuance {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.rel-cell__affix {
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

.rel-cell__nuance-label {
  font-size: 0.75rem;
  line-height: 1.2;
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--color-surface-2, rgba(127, 127, 127, 0.16));
  color: var(--color-text);
  white-space: nowrap;
}

.rel-cell__nuance-select {
  font-size: 0.75rem;
  line-height: 1.2;
  padding: 1px 4px;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  background: var(--color-surface);
  color: var(--color-text);
  max-width: 140px;
}
</style>

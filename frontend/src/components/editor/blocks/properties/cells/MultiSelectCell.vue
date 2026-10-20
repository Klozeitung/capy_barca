<script setup lang="ts">
/**
 * MultiSelectCell
 *
 * Renders currently selected options as chips. When isActive a floating
 * checkbox picker (teleported to <body>) allows toggling individual options.
 *
 * Picker position is computed at open time from the cell's bounding rect so
 * it never overflows the viewport.
 *
 * The value stores option ids, { options: [id, ...] }, so renaming an option
 * never touches it. Chips show the labels the property's config gives them.
 *
 * While the store reports the cell as pending, the picker shows a spinner and
 * takes no toggles, and chips cannot be removed: every change builds on the
 * value the server has just confirmed.
 *
 * Without write access (``useEditable``) the value is only displayed: the
 * cell does not activate and the timeline editor does not open.
 */
import { ref, computed, nextTick, watch, onUnmounted } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import {
  useDatabaseStore,
  type DatabaseEntry,
  type PropertySchema,
  type SelectOption,
  newOptionId,
  optionColorStyle,
  schemaOptions,
} from '@/stores/database'
import { useEscapeKey } from '@/composables/useEscapeStack'
import { getCellValue, getTimelineDisplayMode } from './cellUtils'
import { useEditable } from '@/composables/useEditable'
import TimelineEditor from './TimelineEditor.vue'
import TimelineSlotList from './TimelineSlotList.vue'

const props = defineProps<{
  entry: DatabaseEntry
  schema: PropertySchema
  databaseId: string
  isActive: boolean
}>()

const emit = defineEmits<{
  activate: []
  deactivate: []
}>()

const { t } = useI18n()
const dbStore = useDatabaseStore()
const editable = useEditable()

// ── Timeline ──────────────────────────────────────────────────────────────────

const hasTimeline = () => !!(props.schema.config?.hasTimeline)
const timelineOpen = ref(false)
const anchorRect = ref<DOMRect | undefined>()

// ── Picker position ───────────────────────────────────────────────────────────

const pickerPos = ref({ top: 0, left: 0 })
const cellEl = ref<HTMLElement | null>(null)
const pickerEl = ref<HTMLElement | null>(null)
const searchInputEl = ref<HTMLInputElement | null>(null)
const searchQuery = ref('')
const isCreating = ref(false)
const activeIndex = ref(-1)

// ── Click-away ────────────────────────────────────────────────────────────────

function onDocumentClick(event: MouseEvent) {
  const target = event.target as Node | null
  if (pickerEl.value?.contains(target)) return
  if (cellEl.value?.contains(target)) return
  emit('deactivate')
}

watch(() => props.isActive, async (active) => {
  if (active) {
    await nextTick()
    document.addEventListener('click', onDocumentClick)
  } else {
    document.removeEventListener('click', onDocumentClick)
  }
})

onUnmounted(() => {
  document.removeEventListener('click', onDocumentClick)
})

// Close the picker on Escape via the shared overlay stack, so it wins over any
// SideView underneath it and focus position (cell vs. picker input) no longer
// matters. Registered only while the picker is open.
useEscapeKey(() => emit('deactivate'), computed(() => props.isActive))

async function openPicker(e: MouseEvent) {
  e.stopPropagation()
  if (!editable.value) return
  if (hasTimeline()) {
    anchorRect.value = (e.currentTarget as HTMLElement).getBoundingClientRect()
    timelineOpen.value = true
    return
  }
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
  let top = rect.bottom + 2
  let left = rect.left
  if (left + 200 > window.innerWidth - 8) left = window.innerWidth - 208
  if (top + 280 > window.innerHeight - 8) top = rect.top - 282
  pickerPos.value = { top, left }
  searchQuery.value = ''
  emit('activate')
  await nextTick()
  searchInputEl.value?.focus()
}

// ── Pending write ─────────────────────────────────────────────────────────────

/** A write of this cell is waiting for the server; no further edits meanwhile. */
const isPending = computed(() => dbStore.isCellPending(props.entry.id, props.schema.id))

// ── Value helpers ─────────────────────────────────────────────────────────────

/** Ids of the selected options, in the order they were chosen. */
function selectedValues(): string[] {
  const val = getCellValue(props.entry, props.schema.id, props.schema)
  return (val?.options as string[] | undefined) ?? []
}

function availableOptions(): SelectOption[] {
  return schemaOptions(props.schema)
}

/** The selected options that the property offers, in the order chosen. */
function selectedOptions(): SelectOption[] {
  const byId = new Map(availableOptions().map((o) => [o.id, o]))
  return selectedValues()
    .map((id) => byId.get(id))
    .filter((o): o is SelectOption => o !== undefined)
}

const filteredOptions = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return availableOptions()
  return availableOptions().filter(o => o.label.toLowerCase().includes(q))
})

const showCreate = computed(() => {
  const q = searchQuery.value.trim()
  if (!q) return false
  return !availableOptions().some(o => o.label.toLowerCase() === q.toLowerCase())
})

watch(filteredOptions, () => { activeIndex.value = -1 })

function itemCount(): number {
  return filteredOptions.value.length + (showCreate.value ? 1 : 0)
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

async function toggleOption(id: string) {
  if (isPending.value) return
  const current = selectedValues()
  const idx = current.indexOf(id)
  const updated = idx === -1
    ? [...current, id]
    : current.filter((_, i) => i !== idx)
  await dbStore.upsertValue(
    props.databaseId,
    props.entry.id,
    props.schema.id,
    updated.length > 0 ? { options: updated } : null,
  )
}

async function removeOption(id: string, event: MouseEvent) {
  event.stopPropagation()
  if (isPending.value) return
  const updated = selectedValues().filter(v => v !== id)
  await dbStore.upsertValue(
    props.databaseId,
    props.entry.id,
    props.schema.id,
    updated.length > 0 ? { options: updated } : null,
  )
}

async function createAndToggle() {
  const label = searchQuery.value.trim()
  if (!label || isCreating.value || isPending.value) return
  isCreating.value = true
  try {
    // The id is chosen here so the new option can be selected right away;
    // the backend keeps ids it is given.
    const created = { id: newOptionId(), label }
    await dbStore.updateSchema(props.databaseId, props.schema.id, {
      config: { ...props.schema.config, options: [...availableOptions(), created] },
    })
    const current = selectedValues()
    await dbStore.upsertValue(
      props.databaseId,
      props.entry.id,
      props.schema.id,
      { options: [...current, created.id] },
    )
    searchQuery.value = ''
    await nextTick()
    searchInputEl.value?.focus()
  } finally {
    isCreating.value = false
  }
}
</script>

<template>
  <!-- "all" mode: slot-grouped timeline display -->
  <div
    v-if="hasTimeline() && getTimelineDisplayMode(schema) === 'all'"
    class="db__cell-timeline-all"
    @click.stop="openPicker"
  >
    <TimelineSlotList :entry="entry" :schema="schema" />
    <Icon icon="mdi:clock-outline" width="11" height="11" class="db__timeline-indicator--corner" />
  </div>

  <!-- "last" mode or non-timeline: chip display -->
  <div v-else class="db__ms-wrap" ref="cellEl" @click.stop="openPicker">
    <div class="db__ms-tags">
      <span
        v-for="opt in selectedOptions()"
        :key="opt.id"
        class="db__ms-tag"
        :style="optionColorStyle(opt.color)"
      >
        <span class="db__ms-tag-text">{{ opt.label }}</span>
        <button
          v-if="!hasTimeline()"
          class="db__ms-tag-remove"
          :aria-label="t('actions.remove')"
          @click="removeOption(opt.id, $event)"
        >
          <Icon icon="mdi:close" width="9" height="9" />
        </button>
      </span>
    </div>
    <Icon
      v-if="hasTimeline()"
      icon="mdi:clock-outline"
      width="11"
      height="11"
      class="db__timeline-indicator"
    />
  </div>

  <Teleport to="body">
    <div
      v-if="isActive"
      ref="pickerEl"
      class="db__ms-picker"
      :class="{ 'db__ms-picker--pending': isPending }"
      :style="{ top: pickerPos.top + 'px', left: pickerPos.left + 'px' }"
      :aria-busy="isPending || undefined"
      @click.stop
    >
      <!-- Search input -->
      <div class="db__ms-search-wrap">
        <Icon icon="mdi:magnify" width="13" height="13" class="db__ms-search-icon" />
        <input
          ref="searchInputEl"
          v-model="searchQuery"
          class="db__ms-search"
          :placeholder="t('db.select.searchPlaceholder')"
          @keydown.up.prevent="onKeyNav"
          @keydown.down.prevent="onKeyNav"
          @keydown.enter.prevent="
            activeIndex >= 0
              ? (activeIndex < filteredOptions.length
                  ? toggleOption(filteredOptions[activeIndex].id)
                  : createAndToggle())
              : showCreate
                ? createAndToggle()
                : undefined
          "
          @click.stop
        />
        <span
          v-if="isPending"
          class="db__ms-pending"
          role="status"
          :title="t('db.cellSaving')"
          :aria-label="t('db.cellSaving')"
        >
          <Icon icon="mdi:loading" class="db__ms-spinner" width="13" height="13" />
        </span>
      </div>

      <label
        v-for="(opt, i) in filteredOptions"
        :key="opt.id"
        class="db__ms-option"
        :class="{ 'db__ms-option--focused': activeIndex === i }"
        :data-nav-active="activeIndex === i || undefined"
        @mouseenter="activeIndex = i"
      >
        <input
          type="checkbox"
          :checked="selectedValues().includes(opt.id)"
          :disabled="isPending"
          @change="toggleOption(opt.id)"
        />
        <span class="db__ms-option-chip" :style="optionColorStyle(opt.color)">{{ opt.label }}</span>
      </label>

      <div v-if="availableOptions().length === 0 && !showCreate" class="db__ms-empty">
        {{ t('db.settings.selectOptionsEmpty') }}
      </div>

      <!-- No match — create new option -->
      <button
        v-if="showCreate"
        class="db__ms-create"
        :class="{ 'db__ms-create--focused': activeIndex === filteredOptions.length }"
        :data-nav-active="activeIndex === filteredOptions.length || undefined"
        :disabled="isCreating"
        @click="createAndToggle"
        @mouseenter="activeIndex = filteredOptions.length"
      >
        <Icon icon="mdi:plus-circle-outline" width="13" height="13" />
        {{ t('db.select.create', { query: searchQuery.trim() }) }}
      </button>

      <button class="db__ms-close" @click="emit('deactivate')">
        {{ t('actions.cancel') }}
      </button>
    </div>
  </Teleport>

  <TimelineEditor
    v-if="timelineOpen"
    :entry="entry"
    :schema="schema"
    :database-id="databaseId"
    :anchor-rect="anchorRect"
    @close="timelineOpen = false"
  />
</template>

<style scoped>
.db__cell-timeline-all {
  position: relative;
  cursor: pointer;
  min-height: 36px;
}

.db__timeline-indicator--corner {
  position: absolute;
  top: 4px;
  right: 6px;
  color: var(--color-text-muted);
  opacity: 0.6;
}

.db__ms-wrap {
  display: flex;
  align-items: center;
  min-height: 36px;
  width: 100%;
  cursor: default;
}

.db__timeline-indicator {
  color: var(--color-text-muted);
  flex-shrink: 0;
  opacity: 0.7;
  margin-right: 8px;
  margin-left: auto;
}

.db__ms-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 3px;
  align-items: center;
  padding: 5px 8px;
  min-height: 36px;
}

.db__ms-tag {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  border-radius: 3px;
  padding: 1px 4px 1px 6px;
  font-size: 0.73rem;
  border: 1px solid;
  white-space: nowrap;
}

.db__ms-tag-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.db__ms-tag-remove {
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: none;
  cursor: pointer;
  color: currentColor;
  opacity: 0.55;
  padding: 1px;
  border-radius: 2px;
  flex-shrink: 0;
  transition: opacity 0.12s;
  line-height: 1;
}

.db__ms-tag-remove:hover {
  opacity: 1;
}

.db__ms-option-chip {
  border-radius: 3px;
  padding: 1px 7px;
  font-size: 0.75rem;
  border: 1px solid;
  white-space: nowrap;
}

.db__ms-picker {
  position: fixed;
  z-index: 1000;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 6px;
  padding: 6px;
  min-width: 180px;
  max-height: 280px;
  overflow-y: auto;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.2);
  display: flex;
  flex-direction: column;
  gap: 2px;
}

/*
 * An option is being saved: the list stays readable but takes no clicks
 * until the server has answered. Clicks land on the picker itself, which
 * keeps them from reaching anything underneath.
 */
.db__ms-picker--pending {
  cursor: progress;
}

.db__ms-picker--pending .db__ms-option,
.db__ms-picker--pending .db__ms-create {
  pointer-events: none;
  opacity: 0.6;
}

.db__ms-pending {
  display: flex;
  align-items: center;
  flex-shrink: 0;
  color: var(--color-text-muted);
}

@keyframes db-ms-spin {
  to { transform: rotate(360deg); }
}

.db__ms-spinner {
  animation: db-ms-spin 1s linear infinite;
}

.db__ms-search-wrap {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px;
  border-bottom: 1px solid var(--color-border);
  margin-bottom: 2px;
  flex-shrink: 0;
}

.db__ms-search-icon {
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.db__ms-search {
  flex: 1;
  border: none;
  outline: none;
  background: transparent;
  font-size: 0.8rem;
  color: var(--color-text);
  min-width: 0;
}

.db__ms-search::placeholder {
  color: var(--color-text-muted);
}

.db__ms-create {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 8px;
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

.db__ms-create:hover {
  background: var(--color-hover);
}

.db__ms-create:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.db__ms-option {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 8px;
  border-radius: 4px;
  font-size: 0.8rem;
  cursor: pointer;
  transition: background 0.1s;
}

.db__ms-option:hover,
.db__ms-option--focused {
  background: var(--color-hover);
}

.db__ms-empty {
  font-size: 0.8rem;
  color: var(--color-text-muted);
  text-align: center;
  padding: 6px;
  font-style: italic;
}

.db__ms-close {
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

.db__ms-close:hover {
  color: var(--color-text);
}
</style>

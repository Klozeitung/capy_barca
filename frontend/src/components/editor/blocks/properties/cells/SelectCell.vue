<script setup lang="ts">
/**
 * SelectCell
 *
 * Single-select dropdown cell. When isActive a floating custom picker
 * (Teleported to <body>) is shown; otherwise the selected option is
 * displayed as a coloured chip (#27).
 *
 * The picker includes:
 * - A search input that filters available options in real time.
 * - A "Create '<query>'" row when the query does not match any existing
 *   option. Selecting it appends the new option to the schema config and
 *   immediately selects it for this cell.
 *
 * Options stored in config.options as SelectOption[] (or legacy string[]).
 * Value stored in DB as { option: 'label-string' } — unchanged.
 */
import { ref, computed, watch, nextTick, onUnmounted } from 'vue'
import { Icon } from '@iconify/vue'
import { useDatabaseStore, type DatabaseEntry, type PropertySchema, normalizeSelectOption, optionColorStyle } from '@/stores/database'
import { useEscapeKey } from '@/composables/useEscapeStack'
import { getCellValue, getTimelineDisplayMode } from './cellUtils'
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

const dbStore = useDatabaseStore()

// ── Timeline ──────────────────────────────────────────────────────────────────

const hasTimeline = () => !!(props.schema.config?.hasTimeline)
const timelineOpen = ref(false)
const anchorRect = ref<DOMRect | undefined>()

function openTimeline(event: MouseEvent) {
  anchorRect.value = (event.currentTarget as HTMLElement).getBoundingClientRect()
  timelineOpen.value = true
}

// ── Refs ─────────────────────────────────────────────────────────────────────

const cellEl = ref<HTMLElement | null>(null)
const pickerEl = ref<HTMLElement | null>(null)
const searchInputEl = ref<HTMLInputElement | null>(null)
const searchQuery = ref('')
const pickerStyle = ref<Record<string, string>>({})
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
// SideView underneath it regardless of where focus currently sits. Registered
// only while the (non-timeline) picker is on screen.
useEscapeKey(() => emit('deactivate'), computed(() => props.isActive && !hasTimeline()))

// ── Picker positioning ────────────────────────────────────────────────────────

watch(() => props.isActive, async (active) => {
  if (!active) return
  searchQuery.value = ''
  await nextTick()
  if (cellEl.value) {
    const rect = cellEl.value.getBoundingClientRect()
    let top = rect.bottom + 2
    let left = rect.left
    if (left + 220 > window.innerWidth - 8) left = window.innerWidth - 228
    if (top + 280 > window.innerHeight - 8) top = rect.top - 282
    pickerStyle.value = { top: `${top}px`, left: `${left}px` }
  }
  await nextTick()
  searchInputEl.value?.focus()
})

// ── Value helpers ─────────────────────────────────────────────────────────────

function currentLabel(): string {
  return (getCellValue(props.entry, props.schema.id, props.schema)?.option as string | undefined) ?? ''
}

function currentChipStyle(): Record<string, string> {
  const label = currentLabel()
  if (!label) return {}
  const opt = allOptions().find(o => o.label === label)
  return optionColorStyle(opt?.color)
}

function allOptions() {
  return ((props.schema.config?.options as (string | object)[] | undefined) ?? []).map(normalizeSelectOption)
}

const filteredOptions = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return allOptions()
  return allOptions().filter(o => o.label.toLowerCase().includes(q))
})

const showCreate = computed(() => {
  const q = searchQuery.value.trim()
  if (!q) return false
  return !allOptions().some(o => o.label.toLowerCase() === q.toLowerCase())
})

// Reset focused item whenever the list changes.
watch(filteredOptions, () => { activeIndex.value = -1 })

// Total navigable items: filtered options + optional "create" row at the end.
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

// ── Mutations ─────────────────────────────────────────────────────────────────

async function selectOption(label: string) {
  await dbStore.upsertValue(
    props.databaseId,
    props.entry.id,
    props.schema.id,
    label ? { option: label } : null,
  )
  emit('deactivate')
}

async function clearOption() {
  await dbStore.upsertValue(props.databaseId, props.entry.id, props.schema.id, null)
  emit('deactivate')
}

async function createAndSelect() {
  const label = searchQuery.value.trim()
  if (!label || isCreating.value) return
  isCreating.value = true
  try {
    const existing = (props.schema.config?.options as (string | object)[] | undefined) ?? []
    const normalized = existing.map(normalizeSelectOption)
    const updated = [...normalized, { label }]
    await dbStore.updateSchema(props.databaseId, props.schema.id, {
      config: { ...(props.schema.config ?? {}), options: updated },
    })
    await dbStore.upsertValue(
      props.databaseId,
      props.entry.id,
      props.schema.id,
      { option: label },
    )
    emit('deactivate')
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
    @click.stop="openTimeline($event)"
  >
    <TimelineSlotList :entry="entry" :schema="schema" />
    <Icon icon="mdi:clock-outline" width="11" height="11" class="db__timeline-indicator--corner" />
  </div>

  <!-- "last" mode or non-timeline: chip display -->
  <div
    v-else
    ref="cellEl"
    class="db__cell-chip-wrap"
    @click.stop="hasTimeline() ? openTimeline($event) : emit('activate')"
  >
    <span
      v-if="currentLabel()"
      class="db__cell-chip"
      :style="currentChipStyle()"
    >{{ currentLabel() }}</span>
    <Icon
      v-if="hasTimeline()"
      icon="mdi:clock-outline"
      width="11"
      height="11"
      class="db__timeline-indicator"
    />
  </div>

  <!-- Floating picker (Teleported so it escapes overflow:hidden ancestors) -->
  <Teleport to="body">
    <div
      v-if="isActive && !hasTimeline()"
      ref="pickerEl"
      class="db__sc-picker"
      :style="pickerStyle"
      @click.stop
    >
      <!-- Search input -->
      <div class="db__sc-search-wrap">
        <Icon icon="mdi:magnify" width="13" height="13" class="db__sc-search-icon" />
        <input
          ref="searchInputEl"
          v-model="searchQuery"
          class="db__sc-search"
          placeholder="Search..."
          @keydown.up.prevent="onKeyNav"
          @keydown.down.prevent="onKeyNav"
          @keydown.enter.prevent="
            activeIndex >= 0
              ? (activeIndex < filteredOptions.length
                  ? selectOption(filteredOptions[activeIndex].label)
                  : createAndSelect())
              : showCreate
                ? createAndSelect()
                : (filteredOptions[0] && selectOption(filteredOptions[0].label))
          "
          @click.stop
        />
      </div>

      <!-- Clear selection -->
      <button
        v-if="currentLabel()"
        class="db__sc-item db__sc-item--clear"
        @click="clearOption"
      >
        <Icon icon="mdi:close-circle-outline" width="13" height="13" />
        Clear
      </button>

      <!-- Filtered options -->
      <button
        v-for="(opt, i) in filteredOptions"
        :key="opt.label"
        class="db__sc-item"
        :class="{
          'db__sc-item--selected': opt.label === currentLabel(),
          'db__sc-item--focused': activeIndex === i,
        }"
        :data-nav-active="activeIndex === i || undefined"
        @click="selectOption(opt.label)"
        @mouseenter="activeIndex = i"
      >
        <span class="db__sc-chip" :style="optionColorStyle(opt.color)">{{ opt.label }}</span>
        <Icon
          v-if="opt.label === currentLabel()"
          icon="mdi:check"
          width="13"
          height="13"
          class="db__sc-check"
        />
      </button>

      <!-- Empty state (no options configured at all) -->
      <div
        v-if="allOptions().length === 0 && !showCreate"
        class="db__sc-empty"
      >
        No options configured
      </div>

      <!-- No match — create new option -->
      <button
        v-if="showCreate"
        class="db__sc-item db__sc-item--create"
        :class="{ 'db__sc-item--focused': activeIndex === filteredOptions.length }"
        :data-nav-active="activeIndex === filteredOptions.length || undefined"
        :disabled="isCreating"
        @click="createAndSelect"
        @mouseenter="activeIndex = filteredOptions.length"
      >
        <Icon icon="mdi:plus-circle-outline" width="13" height="13" />
        Create "{{ searchQuery.trim() }}"
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
.db__cell-chip-wrap {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 5px 8px;
  min-height: 36px;
  width: 100%;
  cursor: default;
}

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

.db__timeline-indicator {
  color: var(--color-text-muted);
  flex-shrink: 0;
  opacity: 0.7;
  margin-left: auto;
}

.db__cell-chip {
  border-radius: 3px;
  padding: 1px 7px;
  font-size: 0.75rem;
  border: 1px solid;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}

/* ── Picker ──────────────────────────────────────────────────────────────── */
.db__sc-picker {
  position: fixed;
  z-index: 1000;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 6px;
  padding: 4px;
  min-width: 200px;
  max-width: 260px;
  max-height: 280px;
  overflow-y: auto;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.2);
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.db__sc-search-wrap {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 8px;
  border-bottom: 1px solid var(--color-border);
  margin-bottom: 2px;
  flex-shrink: 0;
}

.db__sc-search-icon {
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.db__sc-search {
  flex: 1;
  border: none;
  outline: none;
  background: transparent;
  font-size: 0.8rem;
  color: var(--color-text);
  min-width: 0;
}

.db__sc-search::placeholder {
  color: var(--color-text-muted);
}

.db__sc-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 8px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.8rem;
  color: var(--color-text);
  background: transparent;
  border: none;
  text-align: left;
  width: 100%;
  transition: background 0.1s;
  flex-shrink: 0;
}

.db__sc-item:hover,
.db__sc-item--focused {
  background: var(--color-hover);
}

.db__sc-item--selected {
  color: var(--color-accent);
}

.db__sc-item--clear {
  color: var(--color-text-muted);
  font-size: 0.75rem;
}

.db__sc-item--create {
  color: var(--color-accent);
  border-top: 1px solid var(--color-border);
  margin-top: 2px;
  padding-top: 7px;
}

.db__sc-item--create:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.db__sc-chip {
  border-radius: 3px;
  padding: 1px 7px;
  font-size: 0.75rem;
  border: 1px solid;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.db__sc-check {
  flex-shrink: 0;
  color: var(--color-accent);
}

.db__sc-empty {
  font-size: 0.8rem;
  color: var(--color-text-muted);
  text-align: center;
  padding: 8px;
  font-style: italic;
}
</style>

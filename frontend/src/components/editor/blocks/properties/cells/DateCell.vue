<script setup lang="ts">
/**
 * DateCell
 *
 * Renders a date (or date-range) property. When isActive the native date /
 * datetime-local inputs are shown. Start-date is always present; end-date is
 * shown only when schema.config.hasEndDate is true.
 *
 * When ``schema.config.hasTimeline`` is true, clicking opens the
 * TimelineEditor instead of the inline date picker.
 *
 * Value shape: { start: string, end: string | null }
 */
import { ref } from 'vue'
import { Icon } from '@iconify/vue'
import { useDatabaseStore, type DatabaseEntry, type PropertySchema } from '@/stores/database'
import { vFocus, getCellValue, displayValue, getTimelineDisplayMode } from './cellUtils'
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
  saved: []
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

// ── Config shortcuts ──────────────────────────────────────────────────────────

function hasEndDate(): boolean {
  return (props.schema.config?.hasEndDate as boolean | undefined) ?? false
}

function includeTime(): boolean {
  return (props.schema.config?.includeTime as boolean | undefined) ?? false
}

function inputType(): string {
  return includeTime() ? 'datetime-local' : 'date'
}

function startValue(): string {
  const val = getCellValue(props.entry, props.schema.id, props.schema)
  return (val?.start as string | undefined) ?? ''
}

function endValue(): string {
  const val = getCellValue(props.entry, props.schema.id, props.schema)
  return (val?.end as string | undefined) ?? ''
}

// ── Save ──────────────────────────────────────────────────────────────────────

async function save(field: 'start' | 'end', newVal: string) {
  const current = getCellValue(props.entry, props.schema.id, props.schema) ?? {}
  let start = (current.start as string | undefined) ?? ''
  let end   = (current.end   as string | undefined) ?? ''

  if (field === 'start') {
    start = newVal
    if (!hasEndDate()) end = newVal
    if (end && start && end < start) end = start
  } else {
    end = (newVal && start && newVal < start) ? start : newVal
  }

  const value = start ? { start, end: end || start } : null
  await dbStore.upsertValue(props.databaseId, props.entry.id, props.schema.id, value)
  emit('saved')
}
</script>

<template>
  <div
    v-if="isActive && !hasTimeline()"
    class="db__date-wrap"
    @click.stop
  >
    <!-- Start date (always present) -->
    <input
      :type="inputType()"
      class="db__cell-input db__cell-input--date"
      :value="startValue()"
      v-focus
      @change="save('start', ($event.target as HTMLInputElement).value)"
      @keydown.escape.prevent="emit('deactivate')"
    />

    <!-- End date (only when hasEndDate is configured) -->
    <template v-if="hasEndDate()">
      <span class="db__date-sep">→</span>
      <input
        :type="inputType()"
        class="db__cell-input db__cell-input--date"
        :value="endValue()"
        @change="save('end', ($event.target as HTMLInputElement).value)"
        @keydown.escape.prevent="emit('deactivate')"
      />
    </template>

    <button class="db__date-done" @click="emit('deactivate')">
      <Icon icon="mdi:check" width="13" height="13" />
    </button>
  </div>

  <!-- "all" mode: slot-grouped timeline display -->
  <div
    v-else-if="hasTimeline() && getTimelineDisplayMode(schema) === 'all'"
    class="db__cell-timeline-all"
    @click.stop="openTimeline($event)"
  >
    <TimelineSlotList :entry="entry" :schema="schema" />
    <Icon icon="mdi:clock-outline" width="11" height="11" class="db__timeline-indicator--corner" />
  </div>

  <!-- "last" mode or non-timeline: normal display -->
  <span
    v-else
    class="db__cell-value"
    :class="{ 'db__cell-value--has-timeline': hasTimeline() }"
    @click.stop="hasTimeline() ? openTimeline($event) : emit('activate')"
  >
    {{ displayValue(entry, schema) }}
    <Icon
      v-if="hasTimeline()"
      icon="mdi:clock-outline"
      width="11"
      height="11"
      class="db__timeline-indicator"
    />
  </span>

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
.db__date-wrap {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 2px 4px;
  outline: 2px solid var(--color-accent);
  outline-offset: -2px;
}

.db__cell-input {
  display: block;
  width: 100%;
  padding: 7px 12px;
  background: transparent;
  border: none;
  outline: 2px solid var(--color-accent);
  outline-offset: -2px;
  font-size: 0.875rem;
  color: var(--color-text);
  min-height: 36px;
  box-sizing: border-box;
}

.db__cell-input--date {
  outline: none;
  flex: 1;
  min-width: 0;
  padding: 5px 8px;
}

.db__date-sep {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.db__date-done {
  background: transparent;
  border: none;
  cursor: pointer;
  color: var(--color-accent);
  display: flex;
  align-items: center;
  padding: 2px;
  border-radius: 3px;
  flex-shrink: 0;
}

.db__cell-value {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 7px 12px;
  font-size: 0.875rem;
  color: var(--color-text);
  min-height: 36px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: default;
}

.db__cell-value--has-timeline { cursor: pointer; }

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
}
</style>

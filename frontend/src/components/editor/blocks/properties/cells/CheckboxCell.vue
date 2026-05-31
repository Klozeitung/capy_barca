<script setup lang="ts">
/**
 * CheckboxCell
 *
 * Renders and toggles a boolean checkbox property value.
 * Unlike other cell components this type has no separate edit-mode; the
 * checkbox flips the value immediately on change.
 *
 * When ``schema.config.hasTimeline`` is true, clicking opens the
 * TimelineEditor instead of toggling the value directly.
 */
import { ref } from 'vue'
import { Icon } from '@iconify/vue'
import { useDatabaseStore, type DatabaseEntry, type PropertySchema } from '@/stores/database'
import { getCellValue, getTimelineDisplayMode } from './cellUtils'
import TimelineEditor from './TimelineEditor.vue'
import TimelineSlotList from './TimelineSlotList.vue'

const props = defineProps<{
  entry: DatabaseEntry
  schema: PropertySchema
  databaseId: string
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

// ── Value ─────────────────────────────────────────────────────────────────────

function isChecked(): boolean {
  const val = getCellValue(props.entry, props.schema.id, props.schema)
  return (val?.checked as boolean | undefined) ?? false
}

async function toggle() {
  await dbStore.upsertValue(
    props.databaseId,
    props.entry.id,
    props.schema.id,
    { checked: !isChecked() },
  )
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

  <!-- "last" timeline mode: icon + clock indicator -->
  <span
    v-else-if="hasTimeline()"
    class="db__checkbox-timeline"
    @click.stop="openTimeline($event)"
  >
    <Icon
      :icon="isChecked() ? 'mdi:checkbox-marked' : 'mdi:checkbox-blank-outline'"
      width="16"
      height="16"
      :class="isChecked() ? 'db__check-icon--checked' : 'db__check-icon'"
    />
    <Icon icon="mdi:clock-outline" width="10" height="10" class="db__timeline-indicator" />
  </span>

  <!-- Normal mode: direct checkbox toggle -->
  <input
    v-else
    type="checkbox"
    class="db__checkbox"
    :checked="isChecked()"
    @change.stop="toggle"
    @click.stop
  />

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

.db__checkbox {
  display: block;
  margin: 0 auto;
  width: 16px;
  height: 16px;
  cursor: pointer;
  accent-color: var(--color-accent);
}

.db__checkbox-timeline {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 7px 12px;
  min-height: 36px;
  cursor: pointer;
}

.db__check-icon {
  color: var(--color-text-muted);
}

.db__check-icon--checked {
  color: var(--color-accent);
}

.db__timeline-indicator {
  color: var(--color-text-muted);
  opacity: 0.7;
}
</style>

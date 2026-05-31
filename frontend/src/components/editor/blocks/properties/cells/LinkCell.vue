<script setup lang="ts">
/**
 * LinkCell
 *
 * Shared cell component for email, phone and url property types.
 * In read mode the value is displayed as a clickable anchor with the
 * appropriate protocol prefix (mailto:, tel:, or plain href).
 * In edit mode a type-specific <input> is shown.
 *
 * Value shape: { value: string }
 *
 * Cursor placement: the <a> inside the display span captures its own clicks
 * via @click.stop (to allow link navigation), so onDisplayClick only fires
 * when the user clicks the surrounding padding. In that case cursor-at-end
 * is acceptable. Focus and caret placement use requestAnimationFrame to
 * survive the browser's asynchronous post-focus cursor reset.
 */
import { ref, watch, nextTick } from 'vue'
import { Icon } from '@iconify/vue'
import { useDatabaseStore, type DatabaseEntry, type PropertySchema } from '@/stores/database'
import { displayValue, getTimelineDisplayMode } from './cellUtils'
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

// ── Draft ─────────────────────────────────────────────────────────────────────

const draft = ref('')

// ── Input ref ────────────────────────────────────────────────────────────────

const inputEl = ref<HTMLInputElement | null>(null)

// ── Display span click handler ────────────────────────────────────────────────

function onDisplayClick(event: MouseEvent): void {
  if (hasTimeline()) { openTimeline(event); return }
  emit('activate')
}

// ── Activation watcher ────────────────────────────────────────────────────────

watch(
  () => props.isActive,
  async (active) => {
    if (!active) return
    draft.value = displayValue(props.entry, props.schema)

    // Wait for v-if to mount the input element.
    await nextTick()
    const el = inputEl.value
    if (!el) return

    el.focus()

    // requestAnimationFrame defers setSelectionRange past the browser's own
    // asynchronous focus-triggered cursor placement.
    requestAnimationFrame(() => {
      if (document.activeElement === el) {
        el.setSelectionRange(el.value.length, el.value.length)
      }
    })
  },
)

// ── Input type ────────────────────────────────────────────────────────────────

function inputType(): 'email' | 'tel' | 'url' {
  if (props.schema.type === 'email') return 'email'
  if (props.schema.type === 'phone') return 'tel'
  return 'url'
}

// ── Link ──────────────────────────────────────────────────────────────────────

function hrefPrefix(): string {
  if (props.schema.type === 'email') return 'mailto:'
  if (props.schema.type === 'phone') return 'tel:'
  return ''
}

function linkTarget(): string | undefined {
  return props.schema.type === 'url' ? '_blank' : undefined
}

function linkRel(): string | undefined {
  return props.schema.type === 'url' ? 'noopener noreferrer' : undefined
}

// ── Save ──────────────────────────────────────────────────────────────────────

async function save() {
  emit('deactivate')
  const raw = draft.value.trim()
  const value = raw ? { value: raw } : null
  await dbStore.upsertValue(props.databaseId, props.entry.id, props.schema.id, value)
}

function cancel() {
  emit('deactivate')
}
</script>

<template>
  <input
    v-if="isActive && !hasTimeline()"
    ref="inputEl"
    :type="inputType()"
    class="db__cell-input"
    v-model="draft"
    @blur="save"
    @keydown.enter.prevent="save"
    @keydown.escape.prevent="cancel"
    @click.stop
  />

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
    @click.stop="onDisplayClick($event)"
  >
    <a
      v-if="displayValue(entry, schema) && !hasTimeline()"
      :href="`${hrefPrefix()}${displayValue(entry, schema)}`"
      :target="linkTarget()"
      :rel="linkRel()"
      class="db__cell-link"
      @click.stop
    >
      {{ displayValue(entry, schema) }}
    </a>
    <template v-else>{{ displayValue(entry, schema) }}</template>
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

.db__cell-link {
  color: var(--color-accent);
  text-decoration: none;
  font-size: 0.875rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.db__cell-link:hover {
  text-decoration: underline;
}
</style>

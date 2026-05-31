<script setup lang="ts">
/**
 * TextCell
 *
 * Editable cell for text, number, and all unrecognised fallback property types.
 * In edit mode a plain <input> is shown; on blur/enter the value is saved.
 *
 * When ``schema.config.hasTimeline`` is true the cell shows a clock indicator
 * and opens the TimelineEditor instead of the inline input on click.
 *
 * Number cells are always seeded from the raw numeric value (not the formatted
 * display string) to prevent corruption when Euro formatting is active.
 * The input uses type="text" with inputmode="decimal" so the browser never
 * silently swallows typed characters.
 *
 * Value shapes:
 *   text    → { text: string }
 *   number  → { number: number }
 *   default → { text: string }
 *
 * Cursor placement: caretPositionFromPoint / caretRangeFromPoint is called on
 * the display span while it is still in the DOM (before emit triggers the v-if
 * swap). The resulting offset is applied via requestAnimationFrame so that the
 * browser's own asynchronous focus-cursor update does not override it.
 */
import { ref, watch, nextTick } from 'vue'
import { Icon } from '@iconify/vue'
import { useDatabaseStore, type DatabaseEntry, type PropertySchema } from '@/stores/database'
import { getCellValue, displayValue, getTimelineDisplayMode } from './cellUtils'
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

// ── Input ref & pending caret offset ─────────────────────────────────────────

const inputEl = ref<HTMLInputElement | null>(null)

/**
 * Character offset within the display span's text content at the moment of
 * the activating click. Captured via caretPositionFromPoint / caretRangeFromPoint
 * while the span is still in the DOM. null when undetermined.
 */
let _pendingCaretOffset: number | null = null

// ── Display span click handler ────────────────────────────────────────────────

function onDisplayClick(event: MouseEvent): void {
  if (hasTimeline()) { openTimeline(event); return }

  // Capture the caret position from the span's DOM text node NOW – the span is
  // still mounted at this point. After emit('activate') Vue will schedule the
  // v-if swap, but reactive updates are batched and won't apply until after
  // this handler returns.
  if (props.schema.type !== 'number') {
    _pendingCaretOffset = resolveSpanCaretOffset(event)
  }

  emit('activate')
}

/**
 * Uses caretPositionFromPoint (standard) or caretRangeFromPoint (WebKit) to
 * find the character offset within the span's text node at the click position.
 * Returns null if the click did not land on a text node (e.g. padding area).
 */
function resolveSpanCaretOffset(event: MouseEvent): number | null {
  let clickedNode: Node | null = null
  let clickedOffset = 0

  if ('caretPositionFromPoint' in document) {
    const pos = (document as any).caretPositionFromPoint(event.clientX, event.clientY)
    if (pos) { clickedNode = pos.offsetNode; clickedOffset = pos.offset }
  } else if ('caretRangeFromPoint' in document) {
    const range = (document as any).caretRangeFromPoint(event.clientX, event.clientY)
    if (range) { clickedNode = range.startContainer; clickedOffset = range.startOffset }
  }

  if (!clickedNode || clickedNode.nodeType !== Node.TEXT_NODE) return null
  return clickedOffset
}

// ── Activation watcher ────────────────────────────────────────────────────────

watch(
  () => props.isActive,
  async (active) => {
    if (!active) {
      _pendingCaretOffset = null
      return
    }

    // Seed draft value.
    if (props.schema.type === 'number') {
      const val = getCellValue(props.entry, props.schema.id, props.schema)
      const raw = val?.number as number | undefined
      draft.value = raw !== undefined && raw !== null ? String(raw) : ''
    } else {
      draft.value = displayValue(props.entry, props.schema)
    }

    // Wait for v-if to mount the input element.
    await nextTick()
    const el = inputEl.value
    if (!el) return

    el.focus()

    const offset = _pendingCaretOffset !== null ? _pendingCaretOffset : el.value.length
    _pendingCaretOffset = null

    // requestAnimationFrame defers setSelectionRange past the browser's own
    // asynchronous focus-triggered cursor placement (which would move cursor to
    // end), so the user-intended position takes precedence.
    requestAnimationFrame(() => {
      if (document.activeElement === el) el.setSelectionRange(offset, offset)
    })
  },
)

// ── Save ──────────────────────────────────────────────────────────────────────

let _saving = false

async function save() {
  if (_saving) return
  _saving = true
  emit('deactivate')

  try {
    const raw = draft.value.trim()
    let value: Record<string, unknown> | null = null

    if (raw !== '') {
      switch (props.schema.type) {
        case 'number': {
          const normalised = raw.replace(',', '.')
          const n = parseFloat(normalised)
          value = isNaN(n) ? null : { number: n }
          break
        }
        case 'text':
        default:
          value = { text: raw }
      }
    }

    await dbStore.upsertValue(props.databaseId, props.entry.id, props.schema.id, value)
  } finally {
    _saving = false
  }
}

function cancel() {
  emit('deactivate')
}
</script>

<template>
  <input
    v-if="isActive && !hasTimeline()"
    ref="inputEl"
    type="text"
    :inputmode="schema.type === 'number' ? 'decimal' : 'text'"
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
    <Icon icon="mdi:clock-outline" width="11" height="11" class="db__timeline-indicator db__timeline-indicator--corner" />
  </div>

  <!-- "last" mode or non-timeline: normal display -->
  <span
    v-else
    class="db__cell-value"
    :class="{ 'db__cell-value--has-timeline': hasTimeline() }"
    @click.stop="onDisplayClick($event)"
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

.db__cell-value--has-timeline {
  cursor: pointer;
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
}
</style>

<script setup lang="ts">
/**
 * TimelineEditor
 *
 * Floating panel for editing timeline-enabled property values.
 *
 * Non-relation types
 * ------------------
 * Displays the ``_timeline`` object as an editable table with one row per
 * slot.  Each row has start / end datetime fields and a type-specific inline
 * value editor.  On save the rows are serialised back to a ``_timeline`` dict
 * and upsersted via the store.
 *
 * Relation types
 * --------------
 * Displays the ``relationPool`` as a list of (linked entry, ranges) pairs.
 * Each linked entry may have multiple active date ranges.  The pool is saved
 * directly; the backend recomputes ``_timeline`` automatically.
 *
 * Range key format:
 *   ""                              → always valid (sole entry only)
 *   "→YYYY-MM-DDTHH:MM:SS"         → until date
 *   "YYYY-MM-DDTHH:MM:SS→"         → since date (open-ended)
 *   "YYYY-MM-DDTHH:MM:SS→YYYY-…"   → from – to
 *
 * Positioning: Teleported to <body> and shown as a centered modal over a dimmed
 * backdrop. The panel never exceeds the viewport; overflowing content scrolls
 * within the body. The optional ``anchorRect`` prop is retained for API
 * compatibility but no longer influences placement (#39).
 */
import { ref, computed, onMounted, nextTick, watch } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import {
  useDatabaseStore,
  type DatabaseEntry,
  type PropertySchema,
  normalizeSelectOption,
  optionColorStyle,
} from '@/stores/database'
import { useBlockStore } from '@/stores/blocks'
import { getRawCellValue, getTimelineDisplayMode, getNuanceConfig, formatPeriodKey, type TimelineDisplayMode } from './cellUtils'

// ── Props / emits ─────────────────────────────────────────────────────────────

const props = defineProps<{
  entry: DatabaseEntry
  schema: PropertySchema
  databaseId: string
  /** DOMRect of the triggering element, used for positioning. */
  anchorRect?: DOMRect
}>()

const emit = defineEmits<{
  (e: 'close'): void
}>()

// ── Dependencies ──────────────────────────────────────────────────────────────

const { t } = useI18n()
const dbStore = useDatabaseStore()
const blockStore = useBlockStore()

// ── Display mode ──────────────────────────────────────────────────────────────

const displayMode = ref<TimelineDisplayMode>(getTimelineDisplayMode(props.schema))

async function saveDisplayMode(newMode: TimelineDisplayMode) {
  displayMode.value = newMode
  await dbStore.updateSchema(props.databaseId, props.schema.id, {
    config: { ...(props.schema.config ?? {}), timelineDisplayMode: newMode },
  })
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const isRelation = computed(() => props.schema.type === 'relation')

function schemaOptions() {
  const opts = (props.schema.config?.options as unknown[] | undefined) ?? []
  return opts.map(normalizeSelectOption)
}

function isMultiSelect() {
  return props.schema.type === 'select' && props.schema.config?.mode === 'multiple'
}

function includeTime() {
  return (props.schema.config?.includeTime as boolean | undefined) ?? false
}

function hasEndDate() {
  return (props.schema.config?.hasEndDate as boolean | undefined) ?? false
}

/** Normalise a datetime-local input value to ISO with seconds. */
function normaliseTs(raw: string): string {
  if (!raw) return ''
  // datetime-local omits seconds; append them
  return raw.length === 16 ? raw + ':00' : raw
}

/** Convert a stored ISO timestamp to a value suitable for datetime-local input. */
function tsToInput(ts: string): string {
  if (!ts) return ''
  return ts.slice(0, 16)
}

// ── Slot state (non-relation) ─────────────────────────────────────────────────

interface TimelineSlot {
  id: string
  isAlways: boolean
  startTs: string   // '' → open start
  endTs: string     // '' → open end
  value: Record<string, unknown>
}

let _nextSlotId = 1

function newSlotId() {
  return String(_nextSlotId++)
}

function buildEmptyValue(): Record<string, unknown> {
  switch (props.schema.type) {
    case 'checkbox': return { checked: false }
    case 'number':   return { number: 0 }
    case 'select':
      return isMultiSelect() ? { options: [] } : { option: '' }
    case 'date':     return { start: '', end: '' }
    case 'email':
    case 'phone':
    case 'url':      return { value: '' }
    default:         return { text: '' }
  }
}

function keyFromSlot(s: TimelineSlot): string {
  if (s.isAlways) return ''
  const start = normaliseTs(s.startTs)
  const end   = normaliseTs(s.endTs)
  if (!start && !end) return ''
  if (!start) return `→${end}`
  if (!end)   return `${start}→`
  return `${start}→${end}`
}

function parseKey(key: string): { isAlways: boolean; startTs: string; endTs: string } {
  if (key === '') return { isAlways: true, startTs: '', endTs: '' }
  if (key.includes('→')) {
    const [s, e] = key.split('→', 2)
    return { isAlways: false, startTs: s || '', endTs: e || '' }
  }
  return { isAlways: false, startTs: key, endTs: '' }
}

function initSlots(): TimelineSlot[] {
  const raw = getRawCellValue(props.entry, props.schema.id)
  if (!raw || !('_timeline' in raw)) {
    return [{
      id: newSlotId(),
      isAlways: true,
      startTs: '',
      endTs: '',
      value: (raw as Record<string, unknown> | null) ?? buildEmptyValue(),
    }]
  }
  const timeline = (raw._timeline as Record<string, unknown>) ?? {}
  const keys = Object.keys(timeline)
  if (keys.length === 0) {
    return [{ id: newSlotId(), isAlways: true, startTs: '', endTs: '', value: buildEmptyValue() }]
  }
  // Sort: "→end" first (start = ''), then by startTs ascending
  const sorted = keys.slice().sort((a, b) => {
    const { startTs: as } = parseKey(a)
    const { startTs: bs } = parseKey(b)
    return as < bs ? -1 : as > bs ? 1 : 0
  })
  return sorted.map(k => ({
    id: newSlotId(),
    ...parseKey(k),
    value: (timeline[k] as Record<string, unknown>) ?? buildEmptyValue(),
  }))
}

const slots = ref<TimelineSlot[]>([])

// ── Pool state (relation) ─────────────────────────────────────────────────────

interface PoolEntry {
  uid: string
  ranges: string[]
}

function initPool(): PoolEntry[] {
  const raw = getRawCellValue(props.entry, props.schema.id)
  if (!raw || !('relationPool' in raw)) return []
  const pool = raw.relationPool as Record<string, string[]>
  return Object.entries(pool).map(([uid, ranges]) => ({ uid, ranges: [...ranges] }))
}

const pool = ref<PoolEntry[]>([])
const newPoolUid = ref('')
const newPoolStartTs = ref('')
const newPoolEndTs = ref('')
const newPoolNuance = ref('')

// ── Nuance (per uid × range) ──────────────────────────────────────────────────

const nuanceCfg = computed(() => getNuanceConfig(props.schema))

function initNuancePool(): Record<string, Record<string, string>> {
  const raw = getRawCellValue(props.entry, props.schema.id)
  const np = raw?.nuancePool as Record<string, Record<string, string>> | undefined
  if (!np) return {}
  const out: Record<string, Record<string, string>> = {}
  for (const [uid, ranges] of Object.entries(np)) out[uid] = { ...ranges }
  return out
}

const nuancePool = ref<Record<string, Record<string, string>>>({})

function nuanceValue(uid: string, range: string): string {
  return nuancePool.value[uid]?.[range] ?? ''
}

function setNuance(uid: string, range: string, label: string): void {
  if (label) {
    if (!nuancePool.value[uid]) nuancePool.value[uid] = {}
    nuancePool.value[uid][range] = label
  } else if (nuancePool.value[uid]) {
    delete nuancePool.value[uid][range]
    if (Object.keys(nuancePool.value[uid]).length === 0) delete nuancePool.value[uid]
  }
}

const targetEntries = computed(() => {
  const targetDbId = (props.schema.config?.target_database_id as string | undefined)
    ?? props.databaseId
  return dbStore.getEntries(targetDbId)
})

function targetDbId() {
  return (props.schema.config?.target_database_id as string | undefined) ?? props.databaseId
}

function entryTitle(uid: string): string {
  const e = targetEntries.value.find(x => x.id === uid)
  return (e?.content?.title as string | undefined) || uid.slice(0, 8) + '…'
}

// ── Pool entry picker (search + create-if-missing) ────────────────────────────
//
// Replaces the former <select> dropdown for choosing the linked entry. Mirrors
// the search-and-create pattern used by RelationCell: the field filters the
// target database's entries live and, when the query matches no existing
// title, offers a "create & link" action that creates a new entry on the fly.

const poolSearchEl = ref<HTMLInputElement | null>(null)
const poolComboEl = ref<HTMLElement | null>(null)
const poolSearchQuery = ref('')
const poolPickerOpen = ref(false)
const poolActiveIndex = ref(-1)
const isCreatingPoolEntry = ref(false)

const filteredPoolEntries = computed<DatabaseEntry[]>(() => {
  const q = poolSearchQuery.value.trim().toLowerCase()
  if (!q) return targetEntries.value
  return targetEntries.value.filter(e =>
    ((e.content?.title as string | undefined) ?? '').toLowerCase().includes(q),
  )
})

const showCreatePoolEntry = computed(() => {
  const q = poolSearchQuery.value.trim()
  if (!q) return false
  // Offer "create" only when the query matches no existing entry title exactly.
  return !targetEntries.value.some(
    e => ((e.content?.title as string | undefined) ?? '').toLowerCase() === q.toLowerCase(),
  )
})

watch(filteredPoolEntries, () => { poolActiveIndex.value = -1 })

function poolItemCount(): number {
  return filteredPoolEntries.value.length + (showCreatePoolEntry.value ? 1 : 0)
}

function onPoolKeyNav(e: KeyboardEvent) {
  const count = poolItemCount()
  if (count === 0) return
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    poolActiveIndex.value = (poolActiveIndex.value + 1) % count
    scrollPoolActiveIntoView()
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    poolActiveIndex.value = (poolActiveIndex.value - 1 + count) % count
    scrollPoolActiveIntoView()
  }
}

function scrollPoolActiveIntoView() {
  nextTick(() => {
    const el = poolComboEl.value?.querySelector<HTMLElement>('[data-nav-active="true"]')
    el?.scrollIntoView({ block: 'nearest' })
  })
}

function openPoolPicker() {
  poolPickerOpen.value = true
}

function closePoolPicker() {
  poolPickerOpen.value = false
  poolActiveIndex.value = -1
}

/** Commit an existing target entry as the linked-entry selection. */
function selectPoolUid(entry: DatabaseEntry) {
  newPoolUid.value = entry.id
  poolSearchQuery.value = (entry.content?.title as string | undefined) || t('main.untitled')
  closePoolPicker()
}

/**
 * Create a new entry in the target database with the search query as its title,
 * then select it as the linked entry. Bilateral sync is handled server-side on
 * save via the existing upsert flow.
 */
async function createAndSelectPoolEntry() {
  const title = poolSearchQuery.value.trim()
  if (!title || isCreatingPoolEntry.value) return
  isCreatingPoolEntry.value = true
  try {
    const newEntry = await dbStore.createEntry(targetDbId())
    await blockStore.updateBlock(newEntry.id, { content: { title } })
    await dbStore.fetchEntries(targetDbId())
    newPoolUid.value = newEntry.id
    poolSearchQuery.value = title
    closePoolPicker()
  } finally {
    isCreatingPoolEntry.value = false
  }
}

/** Enter on the search field: pick the focused row, else create when offered. */
function onPoolSearchEnter() {
  if (poolActiveIndex.value >= 0) {
    if (poolActiveIndex.value < filteredPoolEntries.value.length) {
      selectPoolUid(filteredPoolEntries.value[poolActiveIndex.value])
    } else {
      createAndSelectPoolEntry()
    }
  } else if (showCreatePoolEntry.value) {
    createAndSelectPoolEntry()
  }
}

function buildPoolRangeStr(): string {
  const ns = newPoolStartTs.value.trim()
  const ne = newPoolEndTs.value.trim()
  const s = ns.length === 16 ? ns + ':00' : ns
  const e = ne.length === 16 ? ne + ':00' : ne
  if (!s && !e) return ''
  if (!s) return `→${e}`
  if (!e) return `${s}→`
  return `${s}→${e}`
}

function addPoolRange() {
  if (!newPoolUid.value) return
  const range = buildPoolRangeStr()
  const existing = pool.value.find(e => e.uid === newPoolUid.value)
  if (existing) {
    if (!existing.ranges.includes(range)) existing.ranges.push(range)
  } else {
    pool.value.push({ uid: newPoolUid.value, ranges: [range] })
  }
  if (newPoolNuance.value) setNuance(newPoolUid.value, range, newPoolNuance.value)
  newPoolUid.value = ''
  poolSearchQuery.value = ''
  newPoolStartTs.value = ''
  newPoolEndTs.value = ''
  newPoolNuance.value = ''
}

function removePoolRange(uid: string, range: string) {
  const entry = pool.value.find(e => e.uid === uid)
  if (!entry) return
  entry.ranges = entry.ranges.filter(r => r !== range)
  setNuance(uid, range, '')
  if (entry.ranges.length === 0) {
    pool.value = pool.value.filter(e => e.uid !== uid)
    delete nuancePool.value[uid]
  }
}

// ── Slot manipulation ─────────────────────────────────────────────────────────

function addSlot() {
  slots.value.push({
    id: newSlotId(),
    isAlways: false,
    startTs: '',
    endTs: '',
    value: buildEmptyValue(),
  })
}

function removeSlot(id: string) {
  slots.value = slots.value.filter(s => s.id !== id)
}

// ── Value accessors per slot ──────────────────────────────────────────────────

function slotText(slot: TimelineSlot): string {
  const v = slot.value
  if ('text' in v)  return String(v.text ?? '')
  if ('value' in v) return String(v.value ?? '')
  return ''
}

function setSlotText(slot: TimelineSlot, val: string) {
  if (props.schema.type === 'email' || props.schema.type === 'phone' || props.schema.type === 'url') {
    slot.value = { value: val }
  } else {
    slot.value = { text: val }
  }
}

function slotNumber(slot: TimelineSlot): number | undefined {
  const n = slot.value.number
  return typeof n === 'number' ? n : undefined
}

function setSlotNumber(slot: TimelineSlot, val: string) {
  const n = parseFloat(val)
  slot.value = { number: isNaN(n) ? 0 : n }
}

function slotChecked(slot: TimelineSlot): boolean {
  return (slot.value.checked as boolean | undefined) ?? false
}

function setSlotChecked(slot: TimelineSlot, val: boolean) {
  slot.value = { checked: val }
}

function slotOption(slot: TimelineSlot): string {
  return (slot.value.option as string | undefined) ?? ''
}

function setSlotOption(slot: TimelineSlot, val: string) {
  slot.value = { option: val }
}

function slotOptions(slot: TimelineSlot): string[] {
  return (slot.value.options as string[] | undefined) ?? []
}

function toggleSlotOption(slot: TimelineSlot, label: string) {
  const current = slotOptions(slot)
  const next = current.includes(label)
    ? current.filter(x => x !== label)
    : [...current, label]
  slot.value = { options: next }
}

function slotDateStart(slot: TimelineSlot): string {
  return tsToInput((slot.value.start as string | undefined) ?? '')
}

function slotDateEnd(slot: TimelineSlot): string {
  return tsToInput((slot.value.end as string | undefined) ?? '')
}

function setSlotDateStart(slot: TimelineSlot, val: string) {
  slot.value = { ...slot.value, start: val }
}

function setSlotDateEnd(slot: TimelineSlot, val: string) {
  slot.value = { ...slot.value, end: val }
}

// ── Save ──────────────────────────────────────────────────────────────────────

const isSaving = ref(false)

async function save() {
  if (isSaving.value) return
  isSaving.value = true
  try {
    let value: Record<string, unknown> | null

    if (isRelation.value) {
      const poolObj: Record<string, string[]> = {}
      const nuanceObj: Record<string, Record<string, string>> = {}
      for (const entry of pool.value) {
        if (entry.ranges.length === 0) continue
        poolObj[entry.uid] = [...entry.ranges]
        const perRange: Record<string, string> = {}
        for (const r of entry.ranges) {
          const lbl = nuancePool.value[entry.uid]?.[r]
          if (lbl) perRange[r] = lbl
        }
        if (Object.keys(perRange).length > 0) nuanceObj[entry.uid] = perRange
      }
      if (Object.keys(poolObj).length > 0) {
        value = { relationPool: poolObj }
        if (Object.keys(nuanceObj).length > 0) value.nuancePool = nuanceObj
      } else {
        value = null
      }
    } else {
      const timeline: Record<string, unknown> = {}
      for (const slot of slots.value) {
        const key = keyFromSlot(slot)
        timeline[key] = slot.value
      }
      value = Object.keys(timeline).length > 0 ? { _timeline: timeline } : null
    }

    await dbStore.upsertValue(props.databaseId, props.entry.id, props.schema.id, value)
    emit('close')
  } finally {
    isSaving.value = false
  }
}

// ── Dismissal ─────────────────────────────────────────────────────────────────
//
// The editor is a centered modal over a full-viewport backdrop (#39); a click
// on the backdrop closes it (wired in the template), so no document-level
// listener is required.

/**
 * Clicks inside the panel are stopped from reaching the backdrop, so the pool
 * entry picker needs its own dismissal: any in-panel click that lands outside
 * the combobox closes the dropdown. The combobox stops propagation on its own
 * clicks, so this handler only fires for clicks elsewhere in the panel.
 */
function onPanelClick(event: MouseEvent) {
  if (!poolPickerOpen.value) return
  if (poolComboEl.value?.contains(event.target as Node)) return
  closePoolPicker()
}

// ── Lifecycle ─────────────────────────────────────────────────────────────────

onMounted(async () => {
  if (isRelation.value) {
    pool.value = initPool()
    nuancePool.value = initNuancePool()
    const tid = targetDbId()
    if (targetEntries.value.length === 0) {
      await dbStore.fetchEntries(tid)
    }
  } else {
    slots.value = initSlots()
  }
})
</script>

<template>
  <Teleport to="body">
    <!-- #39: dimmed backdrop; clicking it closes the centered editor. -->
    <div class="te__backdrop" @click="emit('close')"></div>
    <div
      class="te"
      @click.stop="onPanelClick"
    >
      <!-- Header -->
      <div class="te__header">
        <Icon icon="mdi:clock-outline" width="14" height="14" class="te__header-icon" />
        <span class="te__header-title">{{ t('db.timeline.title') }}</span>
        <select
          class="te__mode-select"
          :value="displayMode"
          @change="saveDisplayMode(($event.target as HTMLSelectElement).value as TimelineDisplayMode)"
        >
          <option value="last">{{ t('db.timeline.modeLast') }}</option>
          <option value="all">{{ t('db.timeline.modeAll') }}</option>
          <option value="now" disabled>{{ t('db.timeline.modeNow') }}</option>
          <option value="custom" disabled>{{ t('db.timeline.modeCustom') }}</option>
        </select>
        <button class="te__close" @click="emit('close')">
          <Icon icon="mdi:close" width="14" height="14" />
        </button>
      </div>

      <!-- Body -->
      <div class="te__body">

        <!-- ── Non-relation: slot cards ──────────────────────────────────── -->
        <template v-if="!isRelation">

          <!-- Slot cards -->
          <div
            v-for="slot in slots"
            :key="slot.id"
            class="te__slot"
          >
            <!-- Range row: Start → End -->
            <div class="te__slot-range-row">
              <span class="te__range-label">{{ t('db.timeline.start') }}</span>

              <span v-if="slot.isAlways" class="te__open-label te__open-label--padded">
                {{ t('db.timeline.alwaysValid') }}
              </span>
              <input
                v-else
                type="datetime-local"
                class="te__ts-input"
                :value="slot.startTs"
                @change="slot.startTs = ($event.target as HTMLInputElement).value"
              />

              <span class="te__range-sep">→</span>
              <span class="te__range-label">{{ t('db.timeline.end') }}</span>

              <span v-if="slot.isAlways" class="te__open-label te__open-label--padded">—</span>
              <input
                v-else
                type="datetime-local"
                class="te__ts-input"
                :value="slot.endTs"
                @change="slot.endTs = ($event.target as HTMLInputElement).value"
              />

              <button class="te__delete-btn te__delete-btn--slot" @click="removeSlot(slot.id)" :title="t('actions.delete')">
                <Icon icon="mdi:trash-can-outline" width="13" height="13" />
              </button>
            </div>

            <!-- Value row -->
            <div class="te__slot-value-row">
              <span class="te__range-label">{{ t('db.timeline.value') }}</span>

              <!-- text / email / phone / url / fallback -->
              <input
                v-if="schema.type === 'text' || schema.type === 'email' || schema.type === 'phone' || schema.type === 'url'"
                type="text"
                class="te__value-input"
                :value="slotText(slot)"
                @input="setSlotText(slot, ($event.target as HTMLInputElement).value)"
              />

              <!-- number -->
              <input
                v-else-if="schema.type === 'number'"
                type="number"
                class="te__value-input"
                :value="slotNumber(slot)"
                @input="setSlotNumber(slot, ($event.target as HTMLInputElement).value)"
              />

              <!-- checkbox -->
              <input
                v-else-if="schema.type === 'checkbox'"
                type="checkbox"
                class="te__value-checkbox"
                :checked="slotChecked(slot)"
                @change="setSlotChecked(slot, ($event.target as HTMLInputElement).checked)"
              />

              <!-- select (single) -->
              <div v-else-if="schema.type === 'select' && !isMultiSelect()" class="te__select-chips">
                <button
                  v-for="opt in schemaOptions()"
                  :key="opt.label"
                  class="te__chip"
                  :class="{ 'te__chip--active': slotOption(slot) === opt.label }"
                  :style="slotOption(slot) === opt.label ? optionColorStyle(opt.color) : undefined"
                  @click="setSlotOption(slot, opt.label)"
                >
                  {{ opt.label }}
                </button>
              </div>

              <!-- select (multiple) -->
              <div v-else-if="schema.type === 'select' && isMultiSelect()" class="te__select-chips">
                <button
                  v-for="opt in schemaOptions()"
                  :key="opt.label"
                  class="te__chip"
                  :class="{ 'te__chip--active': slotOptions(slot).includes(opt.label) }"
                  :style="slotOptions(slot).includes(opt.label) ? optionColorStyle(opt.color) : undefined"
                  @click="toggleSlotOption(slot, opt.label)"
                >
                  {{ opt.label }}
                </button>
              </div>

              <!-- date -->
              <div v-else-if="schema.type === 'date'" class="te__date-pair">
                <input
                  :type="includeTime() ? 'datetime-local' : 'date'"
                  class="te__value-input"
                  :value="slotDateStart(slot)"
                  @change="setSlotDateStart(slot, ($event.target as HTMLInputElement).value)"
                />
                <template v-if="hasEndDate()">
                  <span class="te__date-arrow">→</span>
                  <input
                    :type="includeTime() ? 'datetime-local' : 'date'"
                    class="te__value-input"
                    :value="slotDateEnd(slot)"
                    @change="setSlotDateEnd(slot, ($event.target as HTMLInputElement).value)"
                  />
                </template>
              </div>

            </div>
          </div>

          <!-- Empty state -->
          <p v-if="slots.length === 0" class="te__empty">
            {{ t('db.timeline.noSlots') }}
          </p>

          <!-- Add slot -->
          <button class="te__add-btn" @click="addSlot">
            <Icon icon="mdi:plus" width="13" height="13" />
            {{ t('db.timeline.addSlot') }}
          </button>
        </template>

        <!-- ── Relation: pool editor ─────────────────────────────────────── -->
        <template v-else>

          <!-- Existing pool entries -->
          <div
            v-for="entry in pool"
            :key="entry.uid"
            class="te__pool-entry"
          >
            <div class="te__pool-uid">
              <Icon icon="mdi:link-variant" width="12" height="12" class="te__pool-icon" />
              {{ entryTitle(entry.uid) }}
            </div>
            <div class="te__pool-ranges">
              <div
                v-for="range in entry.ranges"
                :key="range"
                class="te__pool-range-row"
              >
                <code class="te__pool-range-key">{{ range ? formatPeriodKey(range) : t('db.timeline.alwaysValid') }}</code>
                <span
                  v-if="nuanceCfg && nuanceValue(entry.uid, range)"
                  class="te__pool-nuance-tag"
                >{{ nuanceValue(entry.uid, range) }}</span>
                <button
                  class="te__delete-btn"
                  @click="removePoolRange(entry.uid, range)"
                >
                  <Icon icon="mdi:close" width="11" height="11" />
                </button>
              </div>
            </div>
          </div>

          <p v-if="pool.length === 0" class="te__empty">
            {{ t('db.timeline.noSlots') }}
          </p>

          <!-- Add pool range -->
          <div class="te__pool-add">
            <div ref="poolComboEl" class="te__pool-combobox" @click.stop>
              <div class="te__pool-search-wrap">
                <Icon icon="mdi:magnify" width="13" height="13" class="te__pool-search-icon" />
                <input
                  ref="poolSearchEl"
                  v-model="poolSearchQuery"
                  class="te__pool-search"
                  :placeholder="t('db.timeline.poolSearchPlaceholder')"
                  @focus="openPoolPicker"
                  @input="openPoolPicker"
                  @keydown.up.prevent="onPoolKeyNav"
                  @keydown.down.prevent="onPoolKeyNav"
                  @keydown.enter.prevent="onPoolSearchEnter"
                />
              </div>

              <div v-if="poolPickerOpen" class="te__pool-results">
                <button
                  v-for="(e, i) in filteredPoolEntries"
                  :key="e.id"
                  class="te__pool-result"
                  :class="{
                    'te__pool-result--selected': e.id === newPoolUid,
                    'te__pool-result--focused': poolActiveIndex === i,
                  }"
                  :data-nav-active="poolActiveIndex === i || undefined"
                  @click="selectPoolUid(e)"
                  @mouseenter="poolActiveIndex = i"
                >
                  <Icon
                    :icon="e.id === newPoolUid ? 'mdi:check-circle' : 'mdi:circle-outline'"
                    width="13"
                    height="13"
                  />
                  {{ (e.content?.title as string | undefined) || t('main.untitled') }}
                </button>

                <div
                  v-if="filteredPoolEntries.length === 0 && !showCreatePoolEntry"
                  class="te__pool-results-empty"
                >
                  {{ t('db.timeline.poolNoEntries') }}
                </div>

                <button
                  v-if="showCreatePoolEntry"
                  class="te__pool-result te__pool-result--create"
                  :class="{ 'te__pool-result--focused': poolActiveIndex === filteredPoolEntries.length }"
                  :data-nav-active="poolActiveIndex === filteredPoolEntries.length || undefined"
                  :disabled="isCreatingPoolEntry"
                  @click="createAndSelectPoolEntry"
                  @mouseenter="poolActiveIndex = filteredPoolEntries.length"
                >
                  <Icon icon="mdi:plus-circle-outline" width="13" height="13" />
                  {{ t('db.timeline.poolCreate', { query: poolSearchQuery.trim() }) }}
                </button>
              </div>
            </div>
            <div class="te__pool-range-inputs">
              <div class="te__pool-range-field">
                <span class="te__range-label">{{ t('db.timeline.start') }}</span>
                <input
                  v-model="newPoolStartTs"
                  type="datetime-local"
                  class="te__ts-input"
                />
              </div>
              <span class="te__range-sep">→</span>
              <div class="te__pool-range-field">
                <span class="te__range-label">{{ t('db.timeline.end') }}</span>
                <input
                  v-model="newPoolEndTs"
                  type="datetime-local"
                  class="te__ts-input"
                />
              </div>
              <div v-if="nuanceCfg" class="te__pool-range-field">
                <span class="te__range-label">{{ t('db.timeline.nuance') }}</span>
                <select v-model="newPoolNuance" class="te__pool-nuance">
                  <option value="">{{ t('db.timeline.nuanceNone') }}</option>
                  <option
                    v-for="opt in nuanceCfg.options"
                    :key="opt.label"
                    :value="opt.label"
                  >{{ opt.label }}</option>
                </select>
              </div>
              <button class="te__add-pool-btn" @click="addPoolRange" :title="t('db.timeline.poolAdd')">
                <Icon icon="mdi:plus" width="13" height="13" />
              </button>
            </div>
          </div>
        </template>
      </div>

      <!-- Footer -->
      <div class="te__footer">
        <button class="te__btn te__btn--ghost" @click="emit('close')">
          {{ t('actions.cancel') }}
        </button>
        <button class="te__btn te__btn--primary" :disabled="isSaving" @click="save">
          {{ t('actions.save') }}
        </button>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.te__backdrop {
  position: fixed;
  inset: 0;
  /* #19: must sit above the SideView panel (z-index 900). */
  z-index: 1000;
  background: rgba(0, 0, 0, 0.35);
}

.te {
  /* #39: centered modal that never exceeds the viewport; the body scrolls. */
  position: fixed;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  z-index: 1001;
  width: 480px;
  max-width: calc(100vw - 32px);
  max-height: min(480px, calc(100vh - 32px));
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* Header */
.te__header {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}
.te__header-icon { color: var(--color-text-muted); }
.te__header-title {
  flex: 1;
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.te__close {
  background: none;
  border: none;
  cursor: pointer;
  color: var(--color-text-muted);
  padding: 2px;
  border-radius: 3px;
  display: flex;
  transition: color 0.12s, background 0.12s;
}
.te__close:hover { color: var(--color-text); background: var(--color-hover); }

.te__mode-select {
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 4px;
  padding: 2px 6px;
  font-size: 0.72rem;
  color: var(--color-text);
  cursor: pointer;
  outline: none;
  flex-shrink: 0;
}

/* Body */
.te__body {
  flex: 1;
  overflow-y: auto;
  padding: 8px 0;
}

/* Slot cards */
.te__slot {
  border-bottom: 1px solid var(--color-border);
  padding: 8px 10px;
}
.te__slot:last-of-type { border-bottom: none; }

.te__slot-range-row {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-bottom: 6px;
}

.te__slot-value-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.te__range-label {
  font-size: 0.68rem;
  font-weight: 600;
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  flex-shrink: 0;
  width: 30px;
}

.te__range-sep {
  color: var(--color-text-muted);
  font-size: 0.8rem;
  flex-shrink: 0;
}

.te__delete-btn--slot {
  margin-left: auto;
}

.te__open-label {
  color: var(--color-text-muted);
  font-size: 0.8rem;
  font-style: italic;
}

.te__open-label--padded {
  padding: 0 4px;
}

/* Inputs */
.te__ts-input,
.te__value-input,
.te__pool-range-input {
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 4px;
  padding: 4px 6px;
  font-size: 0.78rem;
  color: var(--color-text);
  width: 100%;
  outline: none;
  transition: border-color 0.12s;
}
.te__ts-input:focus,
.te__value-input:focus,
.te__pool-range-input:focus {
  border-color: var(--color-accent);
}

.te__value-checkbox {
  width: 15px;
  height: 15px;
  accent-color: var(--color-accent);
  cursor: pointer;
  margin: 0 auto;
}

/* Select chips */
.te__select-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 3px;
}
.te__chip {
  padding: 2px 8px;
  border-radius: 12px;
  border: 1px solid var(--color-border);
  background: var(--color-bg);
  font-size: 0.75rem;
  cursor: pointer;
  transition: border-color 0.1s, background 0.1s;
  color: var(--color-text);
}
.te__chip--active {
  border-color: var(--color-accent);
  background: var(--color-accent-subtle);
}

/* Date pair */
.te__date-pair {
  display: flex;
  align-items: center;
  gap: 4px;
}
.te__date-arrow {
  color: var(--color-text-muted);
  font-size: 0.8rem;
}

/* Delete button */
.te__delete-btn {
  background: none;
  border: none;
  cursor: pointer;
  color: var(--color-text-muted);
  padding: 3px;
  border-radius: 3px;
  display: flex;
  transition: color 0.1s, background 0.1s;
}
.te__delete-btn:hover { color: #e05555; background: var(--color-hover); }

/* Empty */
.te__empty {
  padding: 12px 14px;
  font-size: 0.8rem;
  color: var(--color-text-muted);
  margin: 0;
}

/* Add slot button */
.te__add-btn {
  display: flex;
  align-items: center;
  gap: 5px;
  margin: 6px 10px;
  padding: 5px 10px;
  background: none;
  border: 1px dashed var(--color-border);
  border-radius: 5px;
  font-size: 0.78rem;
  color: var(--color-text-muted);
  cursor: pointer;
  transition: border-color 0.12s, color 0.12s;
}
.te__add-btn:hover { border-color: var(--color-accent); color: var(--color-text); }

/* Pool editor */
.te__pool-entry {
  padding: 6px 10px;
  border-bottom: 1px solid var(--color-border);
}
.te__pool-uid {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 0.8rem;
  font-weight: 500;
  color: var(--color-text);
  margin-bottom: 4px;
}
.te__pool-icon { color: var(--color-text-muted); }
.te__pool-ranges { display: flex; flex-direction: column; gap: 3px; padding-left: 17px; }
.te__pool-range-row {
  display: flex;
  align-items: center;
  gap: 6px;
}
.te__pool-range-key {
  font-size: 0.72rem;
  font-family: var(--font-mono, monospace);
  background: var(--color-hover);
  padding: 2px 6px;
  border-radius: 3px;
  color: var(--color-text-muted);
  flex: 1;
}
.te__pool-add {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 10px;
  border-top: 1px solid var(--color-border);
}
.te__pool-combobox {
  position: relative;
  width: 100%;
}
.te__pool-search-wrap {
  display: flex;
  align-items: center;
  gap: 6px;
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 4px;
  padding: 5px 8px;
  transition: border-color 0.12s;
}
.te__pool-search-wrap:focus-within {
  border-color: var(--color-accent);
}
.te__pool-search-icon {
  color: var(--color-text-muted);
  flex-shrink: 0;
}
.te__pool-search {
  flex: 1;
  border: none;
  outline: none;
  background: transparent;
  font-size: 0.78rem;
  color: var(--color-text);
  min-width: 0;
}
.te__pool-search::placeholder {
  color: var(--color-text-muted);
}
.te__pool-results {
  position: absolute;
  top: calc(100% + 2px);
  left: 0;
  right: 0;
  z-index: 1;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 6px;
  padding: 4px;
  max-height: 200px;
  overflow-y: auto;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.2);
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.te__pool-result {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  border-radius: 4px;
  border: none;
  background: transparent;
  cursor: pointer;
  font-size: 0.78rem;
  color: var(--color-text);
  text-align: left;
  width: 100%;
  transition: background 0.1s;
}
.te__pool-result:hover,
.te__pool-result--focused {
  background: var(--color-hover);
}
.te__pool-result--selected {
  color: var(--color-accent);
}
.te__pool-result--create {
  border-top: 1px solid var(--color-border);
  color: var(--color-accent);
  margin-top: 2px;
  padding-top: 7px;
}
.te__pool-result--create:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.te__pool-results-empty {
  font-size: 0.78rem;
  color: var(--color-text-muted);
  text-align: center;
  padding: 8px;
  font-style: italic;
}
.te__pool-range-inputs {
  display: flex;
  align-items: center;
  gap: 4px;
}
.te__pool-range-field {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.te__add-pool-btn {
  background: var(--color-accent-subtle);
  border: 1px solid var(--color-accent);
  border-radius: 4px;
  padding: 5px 8px;
  cursor: pointer;
  color: var(--color-accent);
  display: flex;
  align-items: center;
  flex-shrink: 0;
  transition: background 0.12s;
}
.te__add-pool-btn:hover { background: var(--color-accent); color: white; }

/* Footer */
.te__footer {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  padding: 10px 12px;
  border-top: 1px solid var(--color-border);
  flex-shrink: 0;
}
.te__btn {
  padding: 6px 14px;
  border-radius: 5px;
  font-size: 0.82rem;
  cursor: pointer;
  transition: background 0.12s, border-color 0.12s;
}
.te__btn--ghost {
  background: none;
  border: 1px solid var(--color-border);
  color: var(--color-text-muted);
}
.te__btn--ghost:hover { background: var(--color-hover); }
.te__btn--primary {
  background: var(--color-accent);
  border: 1px solid var(--color-accent);
  color: white;
}
.te__btn--primary:hover { opacity: 0.88; }
.te__btn--primary:disabled { opacity: 0.5; cursor: default; }

/* ── Nuance (creation select + read-only tag on existing rows) ─────────────── */
.te__pool-nuance {
  padding: 3px 6px;
  font-size: 0.8rem;
  border: 1px solid var(--color-border);
  border-radius: 5px;
  background: var(--color-surface);
  color: var(--color-text);
}

.te__pool-nuance-tag {
  font-size: 0.72rem;
  font-style: italic;
  color: var(--color-text-muted);
  white-space: nowrap;
}
</style>

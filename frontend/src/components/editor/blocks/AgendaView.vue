<script setup lang="ts">
/**
 * AgendaView
 *
 * List-based agenda rendering of a database calendar view.
 * Shows entries grouped by date, in chronological order within
 * the currently displayed month.  Empty days are hidden.
 *
 * Sorting per day: all-day entries first, then timed entries by time.
 *
 * Props / emits mirror CalendarView so DatabaseBlock can swap between
 * the two renderers without changes to its own logic.
 *
 * Changes
 * -------
 * #rec  Client-side recurrence expansion.  Recurring entries are expanded
 *       into one occurrence per visible date.  Editing a recurring occurrence
 *       shows RecurrenceActionDialog to choose the affected scope.
 */
import { ref, computed } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { useBlockStore } from '@/stores/blocks'
import { useDatabaseStore, type DatabaseEntry, type DatabaseView, type PropertySchema } from '@/stores/database'
import CalendarFastEditModal from './CalendarFastEditModal.vue'
import RecurrenceActionDialog from './RecurrenceActionDialog.vue'
import { chipStyle } from '@/composables/calendarColors'
import {
  expandEntry,
  isRecurringEntry,
  subtractOneDay,
  type RecurOccurrence,
} from '@/composables/recurrenceUtils'

// ── Props / emits ─────────────────────────────────────────────────────────────

const props = defineProps<{
  entries: DatabaseEntry[]
  schemas: PropertySchema[]
  view: DatabaseView
  databaseId: string
}>()

const emit = defineEmits<{
  (e: 'open-entry', entry: DatabaseEntry): void
  (e: 'add-on-date', dateStr: string): void
  (e: 'update-view', view: DatabaseView): void
  (e: 'refresh'): void
}>()

// ── i18n ──────────────────────────────────────────────────────────────────────

const { t } = useI18n()
const blockStore = useBlockStore()
const dbStore    = useDatabaseStore()

// ── Month navigation ──────────────────────────────────────────────────────────

const now = new Date()
const viewYear  = ref(now.getFullYear())
const viewMonth = ref(now.getMonth() + 1)

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

function prevMonth(): void {
  if (viewMonth.value === 1) { viewMonth.value = 12; viewYear.value-- }
  else viewMonth.value--
}

function nextMonth(): void {
  if (viewMonth.value === 12) { viewMonth.value = 1; viewYear.value++ }
  else viewMonth.value++
}

function goToday(): void {
  viewYear.value  = now.getFullYear()
  viewMonth.value = now.getMonth() + 1
}

const monthLabel = computed(() => `${MONTH_NAMES[viewMonth.value - 1]} ${viewYear.value}`)

// ── Date helpers ──────────────────────────────────────────────────────────────

function toIsoDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

const todayStr = toIsoDate(now)

/** All dates (YYYY-MM-DD) in the displayed month. */
const monthDates = computed<string[]>(() => {
  const y = viewYear.value
  const m = viewMonth.value
  const days = new Date(y, m, 0).getDate()
  const result: string[] = []
  for (let d = 1; d <= days; d++) {
    result.push(`${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`)
  }
  return result
})

// ── Date schema ───────────────────────────────────────────────────────────────

const dateSchema = computed<PropertySchema | null>(() => {
  const id = props.view.calendarDateSchemaId
  if (!id) return null
  return props.schemas.find(s => s.id === id) ?? null
})

const hasDateSchema = computed(() => dateSchema.value !== null)

function isAllDay(entry: DatabaseEntry): boolean {
  const schema = dateSchema.value
  if (!schema) return true
  const val = entry.values[schema.id] as Record<string, unknown> | null
  if (!val) return true
  const start = (val.start as string | undefined) ?? ''
  return !start.includes('T')
}

function entryTime(entry: DatabaseEntry): string {
  const schema = dateSchema.value
  if (!schema) return ''
  const val = entry.values[schema.id] as Record<string, unknown> | null
  if (!val) return ''
  const start = (val.start as string | undefined) ?? ''
  return start.includes('T') ? start.slice(11, 16) : ''
}

function entryEndTime(entry: DatabaseEntry): string {
  const schema = dateSchema.value
  if (!schema) return ''
  const val = entry.values[schema.id] as Record<string, unknown> | null
  if (!val) return ''
  const end = (val.end as string | undefined) ?? ''
  if (!end.includes('T')) return ''
  const start = (val.start as string | undefined) ?? ''
  const startTime = start.includes('T') ? start.slice(11, 16) : ''
  const endTime = end.slice(11, 16)
  return endTime !== startTime ? endTime : ''
}

function entryTitle(entry: DatabaseEntry): string {
  return ((entry.content?.title as string | undefined) ?? '').trim() || t('main.untitled')
}

function chipColorStyle(entry: DatabaseEntry) {
  const colorKey = (props.view.calendarChipColors ?? {})[entry.id]
  return chipStyle(colorKey)
}

// ── Recurrence expansion (#rec) ───────────────────────────────────────────────

const expandedOccurrences = computed<RecurOccurrence[]>(() => {
  const schema = dateSchema.value
  if (!schema) return []
  const dates = monthDates.value
  if (!dates.length) return []
  const ws = dates[0]
  const we = dates[dates.length - 1]
  const result: RecurOccurrence[] = []
  for (const entry of props.entries) {
    result.push(...expandEntry(entry, schema, ws, we))
  }
  return result
})

const entriesByDate = computed<Record<string, RecurOccurrence[]>>(() => {
  const result: Record<string, RecurOccurrence[]> = {}
  for (const occ of expandedOccurrences.value) {
    if (!result[occ.startDate]) result[occ.startDate] = []
    result[occ.startDate].push(occ)
  }
  // Sort each day: all-day first, then timed by time
  for (const ds of Object.keys(result)) {
    result[ds].sort((a, b) => {
      const aAllDay = isAllDay(a.entry)
      const bAllDay = isAllDay(b.entry)
      if (aAllDay && !bAllDay) return -1
      if (!aAllDay && bAllDay) return 1
      return entryTime(a.entry).localeCompare(entryTime(b.entry))
    })
  }
  return result
})

/** Only days in the month that have at least one occurrence, in order. */
const activeDates = computed<string[]>(() =>
  monthDates.value.filter(ds => (entriesByDate.value[ds]?.length ?? 0) > 0),
)

function dayLabel(dateStr: string): string {
  const [y, m, d] = dateStr.split('-').map(Number)
  const date = new Date(y, m - 1, d)
  const weekdays = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
  const months   = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
  return `${weekdays[date.getDay()]}, ${months[m - 1]} ${d}`
}

function isToday(dateStr: string): boolean { return dateStr === todayStr }

// ── Fast-edit modal ───────────────────────────────────────────────────────────

const editingEntry = ref<DatabaseEntry | null>(null)

// ── Recurrence action dialog (#rec) ──────────────────────────────────────────

const recurDialogOcc  = ref<RecurOccurrence | null>(null)
const recurDialogMode = ref<'edit' | 'delete'>('edit')

function requestEdit(occ: RecurOccurrence): void {
  if (!isRecurringEntry(occ.entry, dateSchema.value)) {
    editingEntry.value = occ.entry
    return
  }
  recurDialogMode.value = 'edit'
  recurDialogOcc.value  = occ
}

function onRecurCancel(): void {
  recurDialogOcc.value = null
}

function onRecurAll(): void {
  const occ = recurDialogOcc.value
  recurDialogOcc.value = null
  if (!occ) return
  // Agenda has no delete; mode is always 'edit' here.
  editingEntry.value = occ.entry
}

async function onRecurThis(): Promise<void> {
  const occ = recurDialogOcc.value
  recurDialogOcc.value = null
  if (!occ) return

  const schema = dateSchema.value
  if (!schema) return

  const rawVal  = (occ.entry.values[schema.id] as Record<string, unknown> | null) ?? {}
  const existing = [...((rawVal.repeatExceptions as string[] | undefined) ?? [])]
  if (!existing.includes(occ.startDate)) existing.push(occ.startDate)

  // Add exception + create standalone entry for this date.
  await dbStore.upsertValue(props.databaseId, occ.entry.id, schema.id, {
    ...rawVal,
    repeatExceptions: existing,
  })

  const rawStart = (rawVal.start as string | undefined) ?? ''
  const rawEnd   = (rawVal.end   as string | undefined) ?? ''
  const startTs  = rawStart.includes('T') ? rawStart.slice(10) : ''
  const endTs    = rawEnd.includes('T')   ? rawEnd.slice(10)   : ''
  const newStart = occ.startDate + startTs
  const newEnd   = occ.endDate   + endTs

  const newEntry = await dbStore.createEntry(props.databaseId)
  await blockStore.updateBlock(newEntry.id, {
    content: { ...(occ.entry.content ?? {}) },
  })
  if (occ.entry.icon) {
    await blockStore.updateAppearance(newEntry.id, { icon: occ.entry.icon })
  }
  await dbStore.upsertValue(props.databaseId, newEntry.id, schema.id, {
    start: newStart,
    end:   newEnd,
  })

  editingEntry.value = {
    ...newEntry,
    values: { [schema.id]: { start: newStart, end: newEnd } },
  }
}

async function onRecurFollowing(): Promise<void> {
  const occ = recurDialogOcc.value
  recurDialogOcc.value = null
  if (!occ) return

  const schema = dateSchema.value
  if (!schema) return

  const rawVal  = (occ.entry.values[schema.id] as Record<string, unknown> | null) ?? {}
  const newUntil = subtractOneDay(occ.startDate)

  await dbStore.upsertValue(props.databaseId, occ.entry.id, schema.id, {
    ...rawVal,
    repeatUntil: newUntil,
  })

  const rawStart = (rawVal.start as string | undefined) ?? ''
  const rawEnd   = (rawVal.end   as string | undefined) ?? ''
  const startTs  = rawStart.includes('T') ? rawStart.slice(10) : ''
  const endTs    = rawEnd.includes('T')   ? rawEnd.slice(10)   : ''
  const newStart = occ.startDate + startTs
  const newEnd   = occ.endDate   + endTs

  const repeat         = (rawVal.repeat as string | undefined) ?? 'none'
  const repeatInterval = (rawVal.repeatInterval as number | undefined) ?? 1
  const repeatUntil    = (rawVal.repeatUntil as string | undefined) ?? ''

  const newDateVal: Record<string, unknown> = {
    start:  newStart,
    end:    newEnd,
    repeat,
    ...(repeatInterval > 1 ? { repeatInterval } : {}),
    ...(repeatUntil         ? { repeatUntil }    : {}),
  }

  const newEntry = await dbStore.createEntry(props.databaseId)
  await blockStore.updateBlock(newEntry.id, {
    content: { ...(occ.entry.content ?? {}) },
  })
  if (occ.entry.icon) {
    await blockStore.updateAppearance(newEntry.id, { icon: occ.entry.icon })
  }
  await dbStore.upsertValue(props.databaseId, newEntry.id, schema.id, newDateVal)

  editingEntry.value = {
    ...newEntry,
    values: { [schema.id]: newDateVal },
  }
}
</script>

<template>
  <div class="agenda">

    <!-- ── No date property configured ──────────────────────────────────────── -->
    <div v-if="!hasDateSchema" class="agenda__empty">
      <Icon icon="mdi:calendar-question-outline" width="36" height="36" class="agenda__empty-icon" />
      <p class="agenda__empty-title">{{ t('db.calendar.noDateProperty') }}</p>
      <p class="agenda__empty-hint">{{ t('db.calendar.noDatePropertyHint') }}</p>
    </div>

    <template v-else>
      <!-- ── Navigation header ──────────────────────────────────────────────── -->
      <div class="agenda__header">
        <button class="agenda__nav-btn" :aria-label="t('db.calendar.prevMonth')" @click="prevMonth">
          <Icon icon="mdi:chevron-left" width="18" height="18" />
        </button>
        <button class="agenda__month-label" @click="goToday">{{ monthLabel }}</button>
        <button class="agenda__nav-btn" :aria-label="t('db.calendar.nextMonth')" @click="nextMonth">
          <Icon icon="mdi:chevron-right" width="18" height="18" />
        </button>
        <button class="agenda__today-btn" @click="goToday">{{ t('db.calendar.today') }}</button>
      </div>

      <!-- ── Day list ───────────────────────────────────────────────────────── -->
      <div class="agenda__body">
        <div v-if="activeDates.length === 0" class="agenda__no-entries">
          <Icon icon="mdi:calendar-blank-outline" width="28" height="28" />
          <p>{{ t('db.calendar.noEntriesThisMonth') }}</p>
        </div>

        <div
          v-for="dateStr in activeDates"
          :key="dateStr"
          class="agenda__day"
        >
          <!-- Day heading -->
          <div
            class="agenda__day-heading"
            :class="{ 'agenda__day-heading--today': isToday(dateStr) }"
          >
            <span
              class="agenda__day-number"
              :class="{ 'agenda__day-number--today': isToday(dateStr) }"
            >
              {{ parseInt(dateStr.slice(8), 10) }}
            </span>
            <span class="agenda__day-label">{{ dayLabel(dateStr) }}</span>
            <button
              class="agenda__day-add"
              :title="t('db.addRow')"
              @click.stop="emit('add-on-date', dateStr)"
            >
              <Icon icon="mdi:plus" width="13" height="13" />
            </button>
          </div>

          <!-- Entries -->
          <div class="agenda__entries">
            <div
              v-for="occ in entriesByDate[dateStr]"
              :key="occ.entry.id + '::' + occ.startDate"
              class="agenda__entry"
            >
              <!-- Color stripe -->
              <span
                class="agenda__entry-stripe"
                :style="{ background: chipColorStyle(occ.entry).background }"
              />

              <!-- Recurrence indicator (#rec) -->
              <Icon
                v-if="isRecurringEntry(occ.entry, dateSchema)"
                icon="mdi:repeat"
                width="11" height="11"
                class="agenda__entry-icon"
                style="opacity: 0.5"
                :title="t('db.calendar.recurIndicator')"
              />

              <!-- Time badge -->
              <span v-if="isAllDay(occ.entry)" class="agenda__time agenda__time--allday">
                {{ t('db.calendar.allDay') }}
              </span>
              <span v-else class="agenda__time">
                {{ entryTime(occ.entry) }}<template v-if="entryEndTime(occ.entry)"> – {{ entryEndTime(occ.entry) }}</template>
              </span>

              <!-- Icon + Name -->
              <Icon :icon="occ.entry.icon ?? 'mdi:file-outline'" width="13" height="13" class="agenda__entry-icon" />
              <span class="agenda__entry-name" @click.stop="emit('open-entry', occ.entry)">
                {{ entryTitle(occ.entry) }}
              </span>

              <!-- Actions -->
              <button
                class="agenda__entry-edit"
                :title="t('db.calendar.fastEdit')"
                @click.stop="requestEdit(occ)"
              >
                <Icon icon="mdi:pencil-outline" width="13" height="13" />
              </button>
              <button
                class="agenda__entry-open"
                :title="t('db.openEntry')"
                @click.stop="emit('open-entry', occ.entry)"
              >
                <Icon icon="mdi:arrow-top-right" width="13" height="13" />
              </button>
            </div>
          </div>
        </div>
      </div>
    </template>

    <!-- ── Fast-edit modal ───────────────────────────────────────────────────── -->
    <CalendarFastEditModal
      v-if="editingEntry"
      :entry="editingEntry"
      :schemas="schemas"
      :view="view"
      :database-id="databaseId"
      @close="editingEntry = null"
      @update-view="emit('update-view', $event)"
      @refresh="emit('refresh')"
    />

    <!-- ── Recurrence scope dialog (#rec) ────────────────────────────────────── -->
    <RecurrenceActionDialog
      v-if="recurDialogOcc"
      :mode="recurDialogMode"
      @this="onRecurThis"
      @following="onRecurFollowing"
      @all="onRecurAll"
      @cancel="onRecurCancel"
    />

  </div>
</template>

<style scoped>
/* ── Root ─────────────────────────────────────────────────────────────────── */
.agenda {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  overflow: hidden;
  background: var(--color-surface);
}

/* ── Empty state ──────────────────────────────────────────────────────────── */
.agenda__empty, .agenda__no-entries {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 64px 32px;
  text-align: center;
  color: var(--color-text-muted);
}

.agenda__empty-icon { color: var(--color-text-muted); margin-bottom: 4px; }
.agenda__empty-title { font-size: 0.925rem; font-weight: 600; color: var(--color-text); margin: 0; }
.agenda__empty-hint  { font-size: 0.82rem; color: var(--color-text-muted); margin: 0; max-width: 340px; }
.agenda__no-entries p { font-size: 0.85rem; margin: 0; }

/* ── Header ──────────────────────────────────────────────────────────────── */
.agenda__header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.agenda__nav-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 5px;
  background: none;
  color: var(--color-text-muted);
  cursor: pointer;
  transition: background 0.1s, color 0.1s;
  flex-shrink: 0;
}

.agenda__nav-btn:hover { background: var(--color-hover); color: var(--color-text); }

.agenda__month-label {
  flex: 1;
  text-align: center;
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--color-text);
  background: none;
  border: none;
  cursor: pointer;
  border-radius: 5px;
  padding: 4px 8px;
  transition: background 0.1s;
}

.agenda__month-label:hover { background: var(--color-hover); }

.agenda__today-btn {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  background: none;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  padding: 3px 8px;
  cursor: pointer;
  transition: background 0.1s, color 0.1s;
  flex-shrink: 0;
}

.agenda__today-btn:hover { background: var(--color-hover); color: var(--color-text); }

/* ── Body ─────────────────────────────────────────────────────────────────── */
.agenda__body {
  overflow-y: auto;
  flex: 1;
}

/* ── Day group ────────────────────────────────────────────────────────────── */
.agenda__day {
  border-bottom: 1px solid var(--color-border);
}

.agenda__day:last-child { border-bottom: none; }

.agenda__day-heading {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 16px 6px;
  background: var(--color-surface);
  position: sticky;
  top: 0;
  z-index: 1;
}

.agenda__day-heading--today {
  background: var(--color-accent-subtle);
}

.agenda__day-number {
  font-size: 1.3rem;
  font-weight: 700;
  color: var(--color-text-muted);
  line-height: 1;
  width: 36px;
  text-align: center;
  flex-shrink: 0;
}

.agenda__day-number--today {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: var(--color-accent);
  color: #fff;
  font-size: 1rem;
}

.agenda__day-label {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--color-text-muted);
  flex: 1;
}

.agenda__day-add {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: none;
  border-radius: 4px;
  background: none;
  color: var(--color-text-muted);
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s, background 0.1s, color 0.1s;
}

.agenda__day-heading:hover .agenda__day-add { opacity: 1; }
.agenda__day-add:hover { background: var(--color-accent-subtle); color: var(--color-accent); }

/* ── Entries ──────────────────────────────────────────────────────────────── */
.agenda__entries {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 2px 0 6px;
}

.agenda__entry {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 16px 6px 14px;
  transition: background 0.1s;
  cursor: default;
}

.agenda__entry:hover { background: var(--color-hover); }

.agenda__entry-stripe {
  width: 3px;
  height: 20px;
  border-radius: 2px;
  flex-shrink: 0;
}

.agenda__time {
  font-size: 0.72rem;
  font-weight: 600;
  color: var(--color-text-muted);
  width: 100px;
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
}

.agenda__time--allday {
  color: var(--color-accent);
  font-weight: 500;
}

.agenda__entry-icon {
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.agenda__entry-name {
  flex: 1;
  font-size: 0.85rem;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
  min-width: 0;
}

.agenda__entry-name:hover { text-decoration: underline; }

.agenda__entry-edit,
.agenda__entry-open {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  background: none;
  border: none;
  cursor: pointer;
  color: var(--color-text-muted);
  border-radius: 4px;
  opacity: 0;
  transition: opacity 0.15s, color 0.15s, background 0.15s;
  flex-shrink: 0;
}

.agenda__entry:hover .agenda__entry-edit,
.agenda__entry:hover .agenda__entry-open { opacity: 1; }

.agenda__entry-edit:hover { background: var(--color-hover); color: var(--color-text); }
.agenda__entry-open:hover { background: var(--color-hover); color: var(--color-accent); }
</style>

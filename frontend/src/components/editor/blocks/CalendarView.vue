<script setup lang="ts">
/**
 * CalendarView
 *
 * Monthly calendar grid as a database view type.
 *
 * Features
 * --------
 * - Entries pinned to day cells via a configurable date property
 * - Multi-day events rendered as spanning bars across week rows
 * - Chips sorted: all-day entries first, then timed entries by time
 * - Time displayed (grayed) on timed chips
 * - Per-entry chip colors (view.calendarChipColors, white text on solid bg)
 * - Page icon on each chip and bar → IconPicker (Teleport to body)
 * - Edit button on each chip and bar → CalendarFastEditModal
 * - Delete button on each chip and bar with 2-step confirmation (3 s auto-reset)
 * - Scrollable cells on hover (no truncation)
 * - Empty state when no date property is configured
 *
 * Changes
 * -------
 * #51  Chips have flex-shrink: 0 so they are never miniaturised.  The
 *      max-height + overflow-y on .cal-view__chips already clips the container;
 *      chips now keep a fixed height and the area scrolls when full.
 * #54  Multi-day bars now carry the same icon-picker / fast-edit / delete
 *      actions as single-day chips.  overflow: hidden is moved from the bar
 *      container to the name span so action buttons are never clipped.
 * #52  Drag-and-drop: chips and bars are draggable.  Dropping onto a day cell
 *      sets only the date; the time component of start (and the full duration
 *      for multi-day entries) is preserved.  The hovered drop-target cell is
 *      highlighted.  Dropping onto the entry's own current date is a no-op.
 * #57  ICS import: a hidden file input in the calendar header lets the user
 *      pick a .ics file.  All VEVENT blocks are parsed client-side; for each
 *      event a new database entry is created and its date value is written.
 *      The parent is notified via emit('refresh') when done.
 * #rec Client-side recurrence expansion.  Entries with repeat != 'none' are
 *      expanded into virtual occurrences for the visible window.  Editing or
 *      deleting a recurring entry shows a RecurrenceActionDialog so the user
 *      can choose to affect just this occurrence, this and following, or all.
 * #92  Fix month-view overflow clipping.  .cal-view__weeks no longer uses
 *      overflow: hidden — the container grows to its natural height and the
 *      page scroll handles the rest.  Cell height changed from a fixed 128px
 *      to min-height: 150px so rows with multi-day bars have room to breathe.
 *      The chip max-height is raised proportionally (72px → 90px).
 * #bar Multi-week bars render their label and actions in every week row.
 *      Previously the icon / name / edit / delete were gated behind a
 *      showLabel flag that was only true in the week where the event visually
 *      started; continuation weeks rendered an empty bar that collapsed to its
 *      padding and looked compressed.  The gate (and the now-unused showLabel
 *      field) is removed, so each week segment of a multi-week event carries
 *      the full content.  .cal-view__bar also gets a min-height floor so every
 *      segment keeps a uniform height even in edge cases.
 */
import { ref, computed } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { useBlockStore } from '@/stores/blocks'
import { useDatabaseStore, type DatabaseEntry, type DatabaseView, type PropertySchema } from '@/stores/database'
import IconPicker from '@/components/IconPicker.vue'
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

const { t } = useI18n()
const blockStore = useBlockStore()
const dbStore    = useDatabaseStore()

// ── Month navigation ──────────────────────────────────────────────────────────

// ── Navigation state ──────────────────────────────────────────────────────────

const now = new Date()
const viewYear  = ref(now.getFullYear())
const viewMonth = ref(now.getMonth() + 1)
/** Used for week/day navigation. Always kept in sync when navigating. */
const viewDay   = ref(now.getDate())

// ── Granularity (#81) ─────────────────────────────────────────────────────────

/** Active granularity: month (default) | week | day. Persisted in the view object. */
const granularity = computed<'month' | 'week' | 'day'>(() =>
  props.view.calendarGranularity ?? 'month',
)

function setGranularity(g: 'month' | 'week' | 'day'): void {
  emit('update-view', { ...props.view, calendarGranularity: g })
}

// ── Period navigation ─────────────────────────────────────────────────────────

function navigateDays(delta: number): void {
  const d = new Date(viewYear.value, viewMonth.value - 1, viewDay.value)
  d.setDate(d.getDate() + delta)
  viewYear.value  = d.getFullYear()
  viewMonth.value = d.getMonth() + 1
  viewDay.value   = d.getDate()
}

function prevPeriod(): void {
  const g = granularity.value
  if (g === 'month') {
    if (viewMonth.value === 1) { viewMonth.value = 12; viewYear.value-- }
    else viewMonth.value--
  } else {
    navigateDays(g === 'week' ? -7 : -1)
  }
}

function nextPeriod(): void {
  const g = granularity.value
  if (g === 'month') {
    if (viewMonth.value === 12) { viewMonth.value = 1; viewYear.value++ }
    else viewMonth.value++
  } else {
    navigateDays(g === 'week' ? 7 : 1)
  }
}

function goToday(): void {
  viewYear.value  = now.getFullYear()
  viewMonth.value = now.getMonth() + 1
  viewDay.value   = now.getDate()
}

const WEEKDAYS    = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su']
const MONTH_NAMES = ['January','February','March','April','May','June',
                     'July','August','September','October','November','December']
const SHORT_MONTHS  = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
const FULL_WEEKDAYS = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday']

// ── Week cells (used for week view and the weeks computed) ────────────────────

const weekCells = computed<CalCell[]>(() => {
  const anchor = new Date(viewYear.value, viewMonth.value - 1, viewDay.value)
  const dow    = (anchor.getDay() + 6) % 7   // 0 = Monday
  const monday = new Date(anchor)
  monday.setDate(anchor.getDate() - dow)
  return Array.from({ length: 7 }, (_, i) => {
    const d  = new Date(monday)
    d.setDate(monday.getDate() + i)
    const ds = toIsoDate(d)
    return {
      dateStr: ds,
      day: d.getDate(),
      isCurrentMonth: true,   // all 7 days are visible/active in week view
      isToday: ds === todayStr,
      isWeekend: d.getDay() === 0 || d.getDay() === 6,
    }
  })
})

// ── Single-day cell (day view) ────────────────────────────────────────────────

const dayCell = computed<CalCell>(() => {
  const d  = new Date(viewYear.value, viewMonth.value - 1, viewDay.value)
  const ds = toIsoDate(d)
  return {
    dateStr: ds,
    day: d.getDate(),
    isCurrentMonth: true,
    isToday: ds === todayStr,
    isWeekend: d.getDay() === 0 || d.getDay() === 6,
  }
})

// ── Period label (replaces monthLabel, adapts to granularity) ────────────────

const periodLabel = computed<string>(() => {
  const g = granularity.value
  if (g === 'month') return `${MONTH_NAMES[viewMonth.value - 1]} ${viewYear.value}`
  if (g === 'week') {
    const cells = weekCells.value
    const [, sm, sd] = cells[0].dateStr.split('-').map(Number)
    const [ey, em, ed] = cells[6].dateStr.split('-').map(Number)
    const tail = (sm !== em)
      ? ` \u2013 ${SHORT_MONTHS[em - 1]} ${ed}, ${ey}`
      : ` \u2013 ${ed}, ${ey}`
    return `${SHORT_MONTHS[sm - 1]} ${sd}${tail}`
  }
  // Day view
  const d = new Date(viewYear.value, viewMonth.value - 1, viewDay.value)
  return `${FULL_WEEKDAYS[d.getDay()]}, ${MONTH_NAMES[viewMonth.value - 1]} ${viewDay.value}, ${viewYear.value}`
})

// ── Nav aria-labels (adapt to granularity) ────────────────────────────────────

const prevAriaLabel = computed(() =>
  granularity.value === 'month' ? t('db.calendar.prevMonth') :
  granularity.value === 'week'  ? t('db.calendar.prevWeek')  :
  t('db.calendar.prevDay'),
)

const nextAriaLabel = computed(() =>
  granularity.value === 'month' ? t('db.calendar.nextMonth') :
  granularity.value === 'week'  ? t('db.calendar.nextWeek')  :
  t('db.calendar.nextDay'),
)

// ── Calendar grid ─────────────────────────────────────────────────────────────

interface CalCell {
  dateStr: string; day: number
  isCurrentMonth: boolean; isToday: boolean; isWeekend: boolean
}

function toIsoDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`
}

const todayStr = toIsoDate(now)

const grid = computed<CalCell[]>(() => {
  const y = viewYear.value, m = viewMonth.value
  const firstDay        = new Date(y, m-1, 1)
  const startOffset     = (firstDay.getDay() + 6) % 7
  const daysInMonth     = new Date(y, m, 0).getDate()
  const daysInPrevMonth = new Date(y, m-1, 0).getDate()
  const cells: CalCell[] = []

  for (let i = startOffset-1; i >= 0; i--) {
    const d=daysInPrevMonth-i, pm=m===1?12:m-1, py=m===1?y-1:y
    const dt=new Date(py,pm-1,d)
    cells.push({ dateStr:toIsoDate(dt), day:d, isCurrentMonth:false, isToday:false, isWeekend:dt.getDay()===0||dt.getDay()===6 })
  }
  for (let d=1; d<=daysInMonth; d++) {
    const dt=new Date(y,m-1,d), ds=toIsoDate(dt)
    cells.push({ dateStr:ds, day:d, isCurrentMonth:true, isToday:ds===todayStr, isWeekend:dt.getDay()===0||dt.getDay()===6 })
  }
  const nm=m===12?1:m+1, ny=m===12?y+1:y
  const remaining = 42 - cells.length
  for (let d=1; d<=remaining; d++) {
    const dt=new Date(ny,nm-1,d)
    cells.push({ dateStr:toIsoDate(dt), day:d, isCurrentMonth:false, isToday:false, isWeekend:dt.getDay()===0||dt.getDay()===6 })
  }
  return cells
})

/**
 * For month view: 6 rows of 7 days each (42 cells from `grid`).
 * For week view: 1 row with the 7 days of the current week.
 * The day view bypasses this computed entirely.
 */
const weeks = computed<CalCell[][]>(() => {
  if (granularity.value !== 'month') return [weekCells.value]
  const c = grid.value
  return [0,1,2,3,4,5].map(i => c.slice(i*7, i*7+7))
})

// ── Date schema + entry helpers ───────────────────────────────────────────────

const dateSchema = computed<PropertySchema | null>(() => {
  const id = props.view.calendarDateSchemaId
  if (!id) return null
  return props.schemas.find(s => s.id === id) ?? null
})

const hasDateSchema = computed(() => dateSchema.value !== null)

function isAllDay(entry: DatabaseEntry): boolean {
  const schema = dateSchema.value
  if (!schema) return true
  const val = entry.values[schema.id] as Record<string,unknown> | null
  if (!val) return true
  return !((val.start as string | undefined) ?? '').includes('T')
}

function entryTime(entry: DatabaseEntry): string {
  const schema = dateSchema.value
  if (!schema) return ''
  const val = entry.values[schema.id] as Record<string,unknown> | null
  if (!val) return ''
  const start = (val.start as string | undefined) ?? ''
  if (!start.includes('T')) return ''
  const time = start.slice(11, 16)
  // Do not show 00:00 – treat as all-day for display purposes (#50)
  return time === '00:00' ? '' : time
}

function entryTitle(entry: DatabaseEntry): string {
  return ((entry.content?.title as string | undefined) ?? '').trim() || t('main.untitled')
}

// ── Occurrence key (unique id for confirm / dialog state) ─────────────────────

function occKey(occ: RecurOccurrence): string {
  return `${occ.entry.id}::${occ.startDate}`
}

// ── Visible date range (drives recurrence expansion window) ──────────────────

const visibleDateRange = computed<[string, string]>(() => {
  const g = granularity.value
  if (g === 'day')  return [dayCell.value.dateStr, dayCell.value.dateStr]
  if (g === 'week') return [weekCells.value[0].dateStr, weekCells.value[6].dateStr]
  // Month: the 42-cell grid always has entries at index 0 and 41.
  return [grid.value[0].dateStr, grid.value[41].dateStr]
})

// ── Recurrence expansion (#rec) ───────────────────────────────────────────────

/**
 * All expanded occurrences for every entry in the current display window.
 * Non-recurring entries produce exactly one occurrence each.
 * Recurring entries produce one per visible occurrence date.
 */
const expandedOccurrences = computed<RecurOccurrence[]>(() => {
  const schema = dateSchema.value
  if (!schema) return []
  const [ws, we] = visibleDateRange.value
  const result: RecurOccurrence[] = []
  for (const entry of props.entries) {
    // Only expand 'date'-typed schemas; readonly time schemas have no repeat data.
    if (schema.type !== 'date') {
      // Fallback: treat as single non-recurring slot.
      const val = entry.values[schema.id] as Record<string, unknown> | null
      if (!val) continue
      const rawStart = (val.start as string ?? (val.value as string | undefined)) ?? ''
      const ds = rawStart.slice(0, 10)
      if (ds && ds >= ws && ds <= we) {
        result.push({ entry, startDate: ds, endDate: ds, isVirtual: false, masterStartDate: ds })
      }
      continue
    }
    result.push(...expandEntry(entry, schema, ws, we))
  }
  return result
})

// ── Single-day entries by date ────────────────────────────────────────────────

/** Occurrences that span exactly one day, keyed by date string. */
const singleDayByDate = computed<Record<string, RecurOccurrence[]>>(() => {
  const result: Record<string, RecurOccurrence[]> = {}
  for (const occ of expandedOccurrences.value) {
    if (occ.endDate > occ.startDate) continue   // skip multi-day
    if (!result[occ.startDate]) result[occ.startDate] = []
    result[occ.startDate].push(occ)
  }
  for (const ds of Object.keys(result)) {
    result[ds].sort((a, b) => {
      const aAD = isAllDay(a.entry), bAD = isAllDay(b.entry)
      if (aAD && !bAD) return -1
      if (!aAD && bAD) return 1
      return entryTime(a.entry).localeCompare(entryTime(b.entry))
    })
  }
  return result
})

function cellEntries(cell: CalCell): RecurOccurrence[] {
  return singleDayByDate.value[cell.dateStr] ?? []
}

// ── Day view occurrences (single-day + multi-day spanning the selected day) ───

const dayViewEntries = computed<RecurOccurrence[]>(() => {
  const ds = dayCell.value.dateStr
  return expandedOccurrences.value
    .filter(occ => occ.startDate <= ds && occ.endDate >= ds)
    .sort((a, b) => {
      const aAD = isAllDay(a.entry), bAD = isAllDay(b.entry)
      if (aAD && !bAD) return -1
      if (!aAD && bAD) return 1
      return entryTime(a.entry).localeCompare(entryTime(b.entry))
    })
})

// ── Multi-day bars per week row ───────────────────────────────────────────────

interface WeekBar {
  occ:       RecurOccurrence
  colStart:  number   // 1-indexed CSS grid column start
  colEnd:    number   // exclusive CSS grid column end
}

function weekBars(weekCellsArr: CalCell[]): WeekBar[] {
  const weekStart = weekCellsArr[0].dateStr
  const weekEnd   = weekCellsArr[6].dateStr
  const bars: WeekBar[] = []

  for (const occ of expandedOccurrences.value) {
    if (occ.endDate <= occ.startDate) continue  // skip single-day

    if (occ.endDate < weekStart || occ.startDate > weekEnd) continue

    const clampedStart = occ.startDate < weekStart ? weekStart : occ.startDate
    const clampedEnd   = occ.endDate   > weekEnd   ? weekEnd   : occ.endDate

    const startIdx = weekCellsArr.findIndex(c => c.dateStr === clampedStart)
    const endIdx   = weekCellsArr.findIndex(c => c.dateStr === clampedEnd)

    const colStart = (startIdx === -1 ? 0 : startIdx) + 1
    const colEnd   = (endIdx   === -1 ? 6 : endIdx)   + 2

    bars.push({ occ, colStart, colEnd })
  }

  return bars
}

// ── Chip color ────────────────────────────────────────────────────────────────

function entryChipStyle(entry: DatabaseEntry) {
  return chipStyle((props.view.calendarChipColors ?? {})[entry.id])
}

// ── Icon picker per chip ──────────────────────────────────────────────────────

const iconPickerEntryId = ref<string | null>(null)
const iconPickerRect    = ref<DOMRect | null>(null)

function openChipIconPicker(entry: DatabaseEntry, e: MouseEvent): void {
  e.stopPropagation()
  iconPickerEntryId.value = entry.id
  iconPickerRect.value    = (e.currentTarget as HTMLElement).getBoundingClientRect()
}

function iconForEntry(entryId: string | null): string | null {
  if (!entryId) return null
  return props.entries.find(e => e.id === entryId)?.icon ?? null
}

async function onChipIconUpdate(newIcon: string | null): Promise<void> {
  const id = iconPickerEntryId.value
  iconPickerEntryId.value = null
  if (!id) return
  await blockStore.updateAppearance(id, { icon: newIcon ?? undefined })
  emit('refresh')
}

// ── Fast-edit modal ───────────────────────────────────────────────────────────

const editingEntry = ref<DatabaseEntry | null>(null)

// ── Recurrence action dialog (#rec) ──────────────────────────────────────────

const recurDialogOcc  = ref<RecurOccurrence | null>(null)
const recurDialogMode = ref<'edit' | 'delete'>('edit')

/** Open the edit modal, routing through the recurrence dialog when needed. */
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

// ── "Edit all" ────────────────────────────────────────────────────────────────

function onRecurAll(): void {
  const occ = recurDialogOcc.value
  recurDialogOcc.value = null
  if (!occ) return

  if (recurDialogMode.value === 'delete') {
    doDeleteEntry(occ.entry.id)
  } else {
    editingEntry.value = occ.entry
  }
}

// ── "Edit / delete this occurrence" ──────────────────────────────────────────

async function onRecurThis(): Promise<void> {
  const occ = recurDialogOcc.value
  recurDialogOcc.value = null
  if (!occ) return

  const schema = dateSchema.value
  if (!schema) return

  const rawVal = (occ.entry.values[schema.id] as Record<string, unknown> | null) ?? {}
  const existing = [...((rawVal.repeatExceptions as string[] | undefined) ?? [])]
  if (!existing.includes(occ.startDate)) existing.push(occ.startDate)

  if (recurDialogMode.value === 'delete') {
    // Add this date to exceptions; master series continues around it.
    await dbStore.upsertValue(props.databaseId, occ.entry.id, schema.id, {
      ...rawVal,
      repeatExceptions: existing,
    })
    emit('refresh')
    return
  }

  // Edit/this: skip this occurrence in the master + create a standalone entry.
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
    // No repeat fields → standalone occurrence.
  })

  // Open the modal for the new entry; use a minimal enriched object so the
  // modal can read date values without waiting for the next refresh cycle.
  editingEntry.value = {
    ...newEntry,
    values: { [schema.id]: { start: newStart, end: newEnd } },
  }
}

// ── "Edit / delete this and following" ───────────────────────────────────────

async function onRecurFollowing(): Promise<void> {
  const occ = recurDialogOcc.value
  recurDialogOcc.value = null
  if (!occ) return

  const schema = dateSchema.value
  if (!schema) return

  const rawVal  = (occ.entry.values[schema.id] as Record<string, unknown> | null) ?? {}
  const newUntil = subtractOneDay(occ.startDate)

  // Truncate master series to end before this occurrence.
  await dbStore.upsertValue(props.databaseId, occ.entry.id, schema.id, {
    ...rawVal,
    repeatUntil: newUntil,
  })

  if (recurDialogMode.value === 'delete') {
    emit('refresh')
    return
  }

  // Edit/following: create a new series starting at this occurrence.
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

// ── Delete with 2-step confirmation (non-recurring only) ─────────────────────

const confirmDeleteId = ref<string | null>(null)
let _confirmTimeout: ReturnType<typeof setTimeout> | null = null

function requestDelete(occ: RecurOccurrence, e: MouseEvent): void {
  e.stopPropagation()

  // Recurring entries skip 2-step and go straight to the scope dialog.
  if (isRecurringEntry(occ.entry, dateSchema.value)) {
    recurDialogMode.value = 'delete'
    recurDialogOcc.value  = occ
    return
  }

  const key = occKey(occ)
  if (confirmDeleteId.value === key) {
    doDeleteEntry(occ.entry.id)
  } else {
    confirmDeleteId.value = key
    if (_confirmTimeout) clearTimeout(_confirmTimeout)
    _confirmTimeout = setTimeout(() => { confirmDeleteId.value = null }, 3000)
  }
}

async function doDeleteEntry(entryId: string): Promise<void> {
  confirmDeleteId.value = null
  if (_confirmTimeout) { clearTimeout(_confirmTimeout); _confirmTimeout = null }
  await blockStore.deleteBlock(entryId, props.databaseId)
  emit('refresh')
}

// ── ICS import (#57) ──────────────────────────────────────────────────────────

const isImporting   = ref(false)

interface IcsEvent {
  summary: string
  start:   string
  end:     string
}

/**
 * Convert an iCalendar date/datetime value to an ISO string.
 *   '20240315'         → '2024-03-15'
 *   '20240315T143000Z' → '2024-03-15T14:30'
 *   '20240315T143000'  → '2024-03-15T14:30'
 */
function icsValueToIso(val: string): string {
  const clean = val.replace(/Z$/, '').trim()
  if (clean.includes('T')) {
    const d = clean.slice(0, 8)
    const t = clean.slice(9, 13)   // skip the 'T' at index 8
    return `${d.slice(0,4)}-${d.slice(4,6)}-${d.slice(6,8)}T${t.slice(0,2)}:${t.slice(2,4)}`
  }
  return `${clean.slice(0,4)}-${clean.slice(4,6)}-${clean.slice(6,8)}`
}

/** Unescape ICS text values (RFC 5545 §3.3.11). */
function icsUnescape(s: string): string {
  return s
    .replace(/\\n/gi, ' ')
    .replace(/\\,/g, ',')
    .replace(/\\;/g, ';')
    .replace(/\\\\/g, '\\')
}

function parseICS(text: string): IcsEvent[] {
  // Unfold continuation lines (RFC 5545 §3.1).
  const lines = text
    .replace(/\r\n/g, '\n').replace(/\r/g, '\n')
    .split('\n')
    .reduce<string[]>((acc, line) => {
      if ((line.startsWith(' ') || line.startsWith('\t')) && acc.length > 0) {
        acc[acc.length - 1] += line.slice(1)
      } else {
        acc.push(line)
      }
      return acc
    }, [])

  const events: IcsEvent[] = []
  let inEvent = false
  let cur: { summary?: string; start?: string; end?: string } = {}

  for (const line of lines) {
    const trimmed = line.trim()
    if (trimmed === 'BEGIN:VEVENT') { inEvent = true; cur = {}; continue }
    if (trimmed === 'END:VEVENT') {
      inEvent = false
      if (cur.start) {
        events.push({
          summary: cur.summary || 'Imported event',
          start:   cur.start,
          // DTEND is optional; fall back to DTSTART for all-day events.
          end:     cur.end ?? cur.start,
        })
      }
      continue
    }
    if (!inEvent) continue

    const colon = trimmed.indexOf(':')
    if (colon < 0) continue
    // Strip parameter sections (e.g. DTSTART;TZID=Europe/Berlin:...)
    const prop  = trimmed.slice(0, colon).split(';')[0].toUpperCase()
    const value = trimmed.slice(colon + 1)

    if (prop === 'SUMMARY')      cur.summary = icsUnescape(value)
    else if (prop === 'DTSTART') cur.start   = icsValueToIso(value)
    else if (prop === 'DTEND')   cur.end     = icsValueToIso(value)
  }
  return events
}

/**
 * #57: Open a file dialog without relying on a hidden DOM input ref.
 * Creating the element dynamically is the most reliable cross-browser approach,
 * especially in Firefox where calling .click() on display:none inputs can be
 * silently ignored.
 */
function triggerIcsImport(): void {
  if (isImporting.value) return
  const input = document.createElement('input')
  input.type   = 'file'
  input.accept = '.ics,text/calendar'
  // Must be part of the document for Firefox to open the dialog.
  input.style.position = 'fixed'
  input.style.top      = '-9999px'
  document.body.appendChild(input)
  input.addEventListener('change', async (e) => {
    await onIcsFileSelected(e)
    document.body.removeChild(input)
  })
  // Clean up if the user cancels without selecting.
  input.addEventListener('cancel', () => document.body.removeChild(input))
  input.click()
}

async function onIcsFileSelected(e: Event): Promise<void> {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return

  isImporting.value = true
  try {
    const text   = await file.text()
    const events = parseICS(text)
    if (events.length === 0) return

    const schema = dateSchema.value

    for (const event of events) {
      const entry = await dbStore.createEntry(props.databaseId)
      await blockStore.updateBlock(entry.id, {
        content: { ...(entry.content ?? {}), title: event.summary },
      })
      if (schema) {
        await dbStore.upsertValue(props.databaseId, entry.id, schema.id, {
          start: event.start,
          end:   event.end,
        })
      }
    }
    emit('refresh')
  } finally {
    isImporting.value = false
  }
}

// ── Drag-and-drop (#52) ───────────────────────────────────────────────────────

/**
 * The entry currently being dragged, or null when not dragging.
 * Only the master occurrence (isVirtual = false) is draggable.
 */
const dragEntry    = ref<DatabaseEntry | null>(null)
const dragOverDate = ref<string | null>(null)

function onEntryDragStart(entry: DatabaseEntry, e: DragEvent): void {
  dragEntry.value = entry
  e.dataTransfer?.setData('text/plain', entry.id)
  if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move'
}

function onEntryDragEnd(): void {
  dragEntry.value    = null
  dragOverDate.value = null
}

function onCellDragOver(cell: CalCell, e: DragEvent): void {
  if (!dragEntry.value) return
  e.preventDefault()
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'move'
  dragOverDate.value = cell.dateStr
}

function onCellDragLeave(e: DragEvent): void {
  const related = e.relatedTarget as Node | null
  if (!(e.currentTarget as HTMLElement).contains(related)) {
    dragOverDate.value = null
  }
}

async function onCellDrop(cell: CalCell): Promise<void> {
  const entry = dragEntry.value
  dragEntry.value    = null
  dragOverDate.value = null
  if (!entry) return

  const schema = dateSchema.value
  if (!schema) return

  const val      = (entry.values[schema.id] as Record<string, unknown> | null) ?? {}
  const rawStart = (val.start as string | undefined) ?? ''
  const rawEnd   = (val.end   as string | undefined) ?? ''
  const startDate = rawStart.slice(0, 10)

  const newStartDate = cell.dateStr
  if (newStartDate === startDate) return

  const timeSuffix = rawStart.includes('T') ? rawStart.slice(10) : ''

  const msPerDay   = 86_400_000
  const oldStartMs = new Date(startDate).getTime()
  const newStartMs = new Date(newStartDate).getTime()
  const deltaDays  = Math.round((newStartMs - oldStartMs) / msPerDay)

  function shiftIso(iso: string): string {
    const dateOnly = iso.slice(0, 10)
    const suffix   = iso.slice(10)
    const d = new Date(dateOnly)
    d.setUTCDate(d.getUTCDate() + deltaDays)
    return toIsoDate(d) + suffix
  }

  const newStart = newStartDate + timeSuffix
  const newEnd   = rawEnd ? shiftIso(rawEnd) : newStart

  await dbStore.upsertValue(props.databaseId, entry.id, schema.id, {
    ...val,
    start: newStart,
    end:   newEnd,
  })
  emit('refresh')
}
</script>

<template>
  <div class="cal-view">

    <!-- ── No date property configured ──────────────────────────────────────── -->
    <div v-if="!hasDateSchema" class="cal-view__empty">
      <Icon icon="mdi:calendar-question-outline" width="36" height="36" class="cal-view__empty-icon" />
      <p class="cal-view__empty-title">{{ t('db.calendar.noDateProperty') }}</p>
      <p class="cal-view__empty-hint">{{ t('db.calendar.noDatePropertyHint') }}</p>
    </div>

    <template v-else>
      <!-- ── Navigation header ─────────────────────────────────────────────── -->
      <div class="cal-view__header">
        <button class="cal-view__nav-btn" :aria-label="prevAriaLabel" @click="prevPeriod">
          <Icon icon="mdi:chevron-left" width="18" height="18" />
        </button>
        <button class="cal-view__month-label" @click="goToday">{{ periodLabel }}</button>
        <button class="cal-view__nav-btn" :aria-label="nextAriaLabel" @click="nextPeriod">
          <Icon icon="mdi:chevron-right" width="18" height="18" />
        </button>
        <button class="cal-view__today-btn" @click="goToday">{{ t('db.calendar.today') }}</button>

        <!-- #81: Granularity switcher (Day / Week / Month) -->
        <div class="cal-view__granularity">
          <button
            class="cal-view__granularity-btn"
            :class="{ 'cal-view__granularity-btn--active': granularity === 'day' }"
            @click="setGranularity('day')"
          >{{ t('db.calendar.granularityDay') }}</button>
          <button
            class="cal-view__granularity-btn"
            :class="{ 'cal-view__granularity-btn--active': granularity === 'week' }"
            @click="setGranularity('week')"
          >{{ t('db.calendar.granularityWeek') }}</button>
          <button
            class="cal-view__granularity-btn"
            :class="{ 'cal-view__granularity-btn--active': granularity === 'month' }"
            @click="setGranularity('month')"
          >{{ t('db.calendar.granularityMonth') }}</button>
        </div>

        <!-- #57: ICS import -->
        <button
          class="cal-view__import-btn"
          :disabled="isImporting"
          :title="t('db.calendar.importIcsTitle')"
          @click="triggerIcsImport"
        >
          <Icon
            :icon="isImporting ? 'mdi:loading' : 'mdi:calendar-import-outline'"
            width="15" height="15"
            :class="{ 'cal-view__spinner': isImporting }"
          />
          {{ t('db.calendar.importIcs') }}
        </button>
      </div>

      <!-- ── Month / Week grid (#81) ────────────────────────────────────────── -->
      <template v-if="granularity !== 'day'">

        <!-- Weekday header: plain labels for month, date-numbered for week -->
        <div
          class="cal-view__weekday-row"
          :class="{ 'cal-view__weekday-row--week': granularity === 'week' }"
        >
          <template v-if="granularity === 'week'">
            <div
              v-for="(cell, i) in weekCells"
              :key="cell.dateStr"
              class="cal-view__weekday cal-view__weekday--dated"
              :class="{ 'cal-view__weekday--today': cell.isToday }"
            >
              <span>{{ WEEKDAYS[i] }}</span>
              <span
                class="cal-view__weekday-date"
                :class="{ 'cal-view__weekday-date--today': cell.isToday }"
              >{{ cell.day }}</span>
            </div>
          </template>
          <template v-else>
            <div v-for="wd in WEEKDAYS" :key="wd" class="cal-view__weekday">{{ wd }}</div>
          </template>
        </div>

        <!-- Week rows (6 for month, 1 for week) -->
        <div
          class="cal-view__weeks"
          :class="{ 'cal-view__weeks--week': granularity === 'week' }"
        >
          <div v-for="(week, wi) in weeks" :key="wi" class="cal-view__week">

            <!-- Multi-day bars layer -->
            <div v-if="weekBars(week).length > 0" class="cal-view__bars-layer">
              <div
                v-for="bar in weekBars(week)"
                :key="bar.occ.entry.id + '::' + bar.occ.startDate + '-' + wi"
                class="cal-view__bar"
                :class="{ 'cal-view__bar--dragging': dragEntry?.id === bar.occ.entry.id }"
                :style="{ ...entryChipStyle(bar.occ.entry), gridColumn: `${bar.colStart} / ${bar.colEnd}` }"
                :title="entryTitle(bar.occ.entry)"
                :draggable="!bar.occ.isVirtual"
                @dragstart="!bar.occ.isVirtual && onEntryDragStart(bar.occ.entry, $event)"
                @dragend="onEntryDragEnd"
              >
                <!-- Rendered in every week segment of a multi-week event, so
                     the name and actions are reachable from any row and no
                     continuation segment collapses to an empty bar. -->

                <!-- Recurrence indicator (#rec) -->
                <Icon
                  v-if="isRecurringEntry(bar.occ.entry, dateSchema)"
                  icon="mdi:repeat"
                  width="8" height="8"
                  class="cal-view__chip-recur"
                  :title="t('db.calendar.recurIndicator')"
                />

                <!-- Page icon → icon picker (#54) -->
                <button class="cal-view__chip-icon-btn" @click.stop="openChipIconPicker(bar.occ.entry, $event)">
                  <Icon :icon="bar.occ.entry.icon ?? 'mdi:file-outline'" width="9" height="9" />
                </button>

                <!-- Name → navigate (#54) -->
                <span class="cal-view__bar-name" @click.stop="emit('open-entry', bar.occ.entry)">
                  {{ entryTitle(bar.occ.entry) }}
                </span>

                <!-- Edit (#54) -->
                <button class="cal-view__chip-action" :title="t('db.calendar.fastEdit')" @click.stop="requestEdit(bar.occ)">
                  <Icon icon="mdi:pencil-outline" width="9" height="9" />
                </button>

                <!-- Delete (#54) -->
                <button
                  class="cal-view__chip-action"
                  :class="{ 'cal-view__chip-action--danger': confirmDeleteId === occKey(bar.occ) }"
                  :title="confirmDeleteId === occKey(bar.occ) ? t('db.calendar.deleteConfirm') : t('actions.delete')"
                  @click.stop="requestDelete(bar.occ, $event)"
                >
                  <Icon :icon="confirmDeleteId === occKey(bar.occ) ? 'mdi:check' : 'mdi:trash-can-outline'" width="9" height="9" />
                </button>
              </div>
            </div>

            <!-- Day cells -->
            <div class="cal-view__day-row">
              <div
                v-for="cell in week"
                :key="cell.dateStr"
                class="cal-view__cell"
                :class="{
                  'cal-view__cell--other-month': !cell.isCurrentMonth,
                  'cal-view__cell--today':       cell.isToday,
                  'cal-view__cell--weekend':     cell.isWeekend && cell.isCurrentMonth,
                  'cal-view__cell--drag-over':   dragOverDate === cell.dateStr && dragEntry !== null,
                }"
                @dragover="onCellDragOver(cell, $event)"
                @dragleave="onCellDragLeave($event)"
                @drop.prevent="onCellDrop(cell)"
              >
                <!-- Cell header -->
                <div class="cal-view__cell-header">
                  <span class="cal-view__day-number" :class="{ 'cal-view__day-number--today': cell.isToday }">
                    {{ cell.day }}
                  </span>
                  <button class="cal-view__add-btn" :title="t('db.addRow')" @click.stop="emit('add-on-date', cell.dateStr)">
                    <Icon icon="mdi:plus" width="12" height="12" />
                  </button>
                </div>

                <!-- Single-day chips (scrollable on hover) -->
                <div class="cal-view__chips">
                  <div
                    v-for="occ in cellEntries(cell)"
                    :key="occ.entry.id + '::' + occ.startDate"
                    class="cal-view__chip"
                    :class="{ 'cal-view__chip--dragging': dragEntry?.id === occ.entry.id }"
                    :style="entryChipStyle(occ.entry)"
                    :title="entryTitle(occ.entry)"
                    :draggable="!occ.isVirtual"
                    @dragstart="!occ.isVirtual && onEntryDragStart(occ.entry, $event)"
                    @dragend="onEntryDragEnd"
                  >
                    <!-- Recurrence indicator (#rec) -->
                    <Icon
                      v-if="isRecurringEntry(occ.entry, dateSchema)"
                      icon="mdi:repeat"
                      width="8" height="8"
                      class="cal-view__chip-recur"
                      :title="t('db.calendar.recurIndicator')"
                    />

                    <!-- Page icon → icon picker -->
                    <button class="cal-view__chip-icon-btn" @click.stop="openChipIconPicker(occ.entry, $event)">
                      <Icon :icon="occ.entry.icon ?? 'mdi:file-outline'" width="10" height="10" />
                    </button>

                    <!-- Name → navigate -->
                    <span class="cal-view__chip-name" @click.stop="emit('open-entry', occ.entry)">
                      {{ entryTitle(occ.entry) }}
                    </span>

                    <!-- Time (timed entries only, grayed) -->
                    <span v-if="entryTime(occ.entry)" class="cal-view__chip-time">{{ entryTime(occ.entry) }}</span>

                    <!-- Edit -->
                    <button class="cal-view__chip-action" :title="t('db.calendar.fastEdit')" @click.stop="requestEdit(occ)">
                      <Icon icon="mdi:pencil-outline" width="9" height="9" />
                    </button>

                    <!-- Delete -->
                    <button
                      class="cal-view__chip-action"
                      :class="{ 'cal-view__chip-action--danger': confirmDeleteId === occKey(occ) }"
                      :title="confirmDeleteId === occKey(occ) ? t('db.calendar.deleteConfirm') : t('actions.delete')"
                      @click.stop="requestDelete(occ, $event)"
                    >
                      <Icon :icon="confirmDeleteId === occKey(occ) ? 'mdi:check' : 'mdi:trash-can-outline'" width="9" height="9" />
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </template>

      <!-- ── Day view (#81) ─────────────────────────────────────────────────── -->
      <template v-else>
        <div class="cal-view__day-view">
          <!-- Day header -->
          <div class="cal-view__dv-header" :class="{ 'cal-view__dv-header--today': dayCell.isToday }">
            <span
              class="cal-view__dv-date-num"
              :class="{ 'cal-view__dv-date-num--today': dayCell.isToday }"
            >{{ dayCell.day }}</span>
            <span class="cal-view__dv-date-label">{{ periodLabel }}</span>
            <button class="cal-view__dv-add" :title="t('db.addRow')" @click.stop="emit('add-on-date', dayCell.dateStr)">
              <Icon icon="mdi:plus" width="13" height="13" />
              {{ t('db.addRow') }}
            </button>
          </div>

          <!-- Empty state -->
          <div v-if="dayViewEntries.length === 0" class="cal-view__dv-empty">
            <Icon icon="mdi:calendar-blank-outline" width="28" height="28" />
            <p>{{ t('db.calendar.noEntriesThisDay') }}</p>
          </div>

          <!-- Entry list -->
          <div
            v-for="occ in dayViewEntries"
            :key="occ.entry.id + '::' + occ.startDate"
            class="cal-view__dv-entry"
          >
            <!-- Color stripe -->
            <span
              class="cal-view__dv-stripe"
              :style="{ background: entryChipStyle(occ.entry).background }"
            />

            <!-- Recurrence indicator (#rec) -->
            <Icon
              v-if="isRecurringEntry(occ.entry, dateSchema)"
              icon="mdi:repeat"
              width="11" height="11"
              class="cal-view__dv-icon"
              style="opacity: 0.5"
              :title="t('db.calendar.recurIndicator')"
            />

            <!-- Time badge -->
            <span
              class="cal-view__dv-time"
              :class="{ 'cal-view__dv-time--allday': isAllDay(occ.entry) }"
            >
              <template v-if="isAllDay(occ.entry)">{{ t('db.calendar.allDay') }}</template>
              <template v-else>{{ entryTime(occ.entry) }}</template>
            </span>

            <!-- Icon -->
            <Icon :icon="occ.entry.icon ?? 'mdi:file-outline'" width="13" height="13" class="cal-view__dv-icon" />

            <!-- Name -->
            <span class="cal-view__dv-name" @click.stop="emit('open-entry', occ.entry)">
              {{ entryTitle(occ.entry) }}
            </span>

            <!-- Edit -->
            <button
              class="cal-view__dv-action"
              :title="t('db.calendar.fastEdit')"
              @click.stop="requestEdit(occ)"
            >
              <Icon icon="mdi:pencil-outline" width="13" height="13" />
            </button>

            <!-- Delete (2-step for non-recurring, dialog for recurring) -->
            <button
              class="cal-view__dv-action"
              :class="{ 'cal-view__dv-action--danger': confirmDeleteId === occKey(occ) }"
              :title="confirmDeleteId === occKey(occ) ? t('db.calendar.deleteConfirm') : t('actions.delete')"
              @click.stop="requestDelete(occ, $event)"
            >
              <Icon :icon="confirmDeleteId === occKey(occ) ? 'mdi:check' : 'mdi:trash-can-outline'" width="13" height="13" />
            </button>

            <!-- Open -->
            <button
              class="cal-view__dv-action"
              :title="t('db.openEntry')"
              @click.stop="emit('open-entry', occ.entry)"
            >
              <Icon icon="mdi:arrow-top-right" width="13" height="13" />
            </button>
          </div>
        </div>
      </template>

    <!-- ── Icon picker (Teleport to body) ────────────────────────────────────── -->
    <Teleport to="body">
      <IconPicker
        v-if="iconPickerEntryId"
        :model-value="iconForEntry(iconPickerEntryId)"
        :trigger-rect="iconPickerRect"
        @update:model-value="onChipIconUpdate"
        @close="iconPickerEntryId = null"
      />
    </Teleport>

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

    </template><!-- end v-else (hasDateSchema) -->

  </div>
</template>

<style scoped>
.cal-view {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  overflow: hidden;
  background: var(--color-surface);
}

/* ── Empty ───────────────────────────────────────────────────────────────── */
.cal-view__empty {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 8px; padding: 64px 32px; text-align: center;
}
.cal-view__empty-icon  { color: var(--color-text-muted); margin-bottom: 4px; }
.cal-view__empty-title { font-size: 0.925rem; font-weight: 600; color: var(--color-text); margin: 0; }
.cal-view__empty-hint  { font-size: 0.82rem; color: var(--color-text-muted); margin: 0; max-width: 340px; }

/* ── Header ──────────────────────────────────────────────────────────────── */
.cal-view__header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.cal-view__nav-btn {
  display: flex; align-items: center; justify-content: center;
  width: 28px; height: 28px; border: none; border-radius: 5px; background: none;
  color: var(--color-text-muted); cursor: pointer; transition: background 0.1s, color 0.1s;
}
.cal-view__nav-btn:hover { background: var(--color-hover); color: var(--color-text); }

.cal-view__month-label {
  flex: 1; text-align: center; font-size: 0.875rem; font-weight: 600; color: var(--color-text);
  background: none; border: none; cursor: pointer; border-radius: 5px; padding: 2px 6px;
  transition: background 0.1s;
}
.cal-view__month-label:hover { background: var(--color-hover); }

.cal-view__today-btn {
  font-size: 0.75rem; color: var(--color-text-muted); background: none;
  border: 1px solid var(--color-border); border-radius: 4px; padding: 3px 8px;
  cursor: pointer; transition: background 0.1s, color 0.1s; flex-shrink: 0;
}
.cal-view__today-btn:hover { background: var(--color-hover); color: var(--color-text); }

/* ── Granularity switcher (#81) ──────────────────────────────────────────── */
.cal-view__granularity {
  display: flex; border: 1px solid var(--color-border); border-radius: 5px; overflow: hidden;
  flex-shrink: 0;
}
.cal-view__granularity-btn {
  font-size: 0.75rem; color: var(--color-text-muted); background: none; border: none;
  padding: 3px 9px; cursor: pointer; transition: background 0.1s, color 0.1s;
}
.cal-view__granularity-btn:not(:last-child) { border-right: 1px solid var(--color-border); }
.cal-view__granularity-btn--active {
  background: var(--color-accent-subtle); color: var(--color-accent); font-weight: 600;
}
.cal-view__granularity-btn:hover:not(.cal-view__granularity-btn--active) {
  background: var(--color-hover); color: var(--color-text);
}

/* ── Import button (#57) ─────────────────────────────────────────────────── */
.cal-view__import-btn {
  display: flex; align-items: center; gap: 4px; font-size: 0.75rem;
  color: var(--color-text-muted); background: none; border: 1px solid var(--color-border);
  border-radius: 4px; padding: 4px 8px; cursor: pointer;
  transition: background 0.1s, color 0.1s; flex-shrink: 0;
}
.cal-view__import-btn:hover:not(:disabled) { background: var(--color-hover); color: var(--color-text); }
.cal-view__import-btn:disabled { opacity: 0.5; cursor: not-allowed; }

@keyframes cal-spin { to { transform: rotate(360deg); } }
.cal-view__spinner { animation: cal-spin 0.8s linear infinite; }

/* ── Weekday row ─────────────────────────────────────────────────────────── */
.cal-view__weekday-row {
  display: grid; grid-template-columns: repeat(7, 1fr);
  background: var(--color-surface); border-bottom: 1px solid var(--color-border);
  padding: 0 6px;
}
.cal-view__weekday {
  text-align: center; font-size: 0.7rem; font-weight: 600;
  color: var(--color-text-muted); padding: 6px 0;
  text-transform: uppercase; letter-spacing: 0.04em;
}
/* Week-view variant: wider label with date number below */
.cal-view__weekday-row--week .cal-view__weekday {
  display: flex; flex-direction: column; align-items: center; gap: 2px; padding: 6px 0 4px;
}
.cal-view__weekday--dated { cursor: default; }
.cal-view__weekday--today { color: var(--color-accent); }
.cal-view__weekday-date {
  font-size: 1rem; font-weight: 700; color: var(--color-text-muted); line-height: 1;
  width: 28px; height: 28px; display: inline-flex; align-items: center; justify-content: center;
  border-radius: 50%;
}
.cal-view__weekday-date--today {
  background: var(--color-accent); color: #fff;
}

/* ── Weeks + bars ────────────────────────────────────────────────────────── */
/* #92: flex: 1 and overflow removed — .db now has flex-shrink: 0 so the
        entire database block grows to content height and .main-view scrolls.
        The weeks container must size to its cells naturally; flex: 1 inside
        an unconstrained parent collapses height, and overflow-y: auto would
        create an unwanted inner scroll area. */
.cal-view__weeks {
  display: flex; flex-direction: column;
}

.cal-view__week { display: flex; flex-direction: column; }

/* Bars layer: CSS grid places each bar at the correct columns. */
.cal-view__bars-layer {
  display: grid; grid-template-columns: repeat(7, 1fr);
  padding: 2px 6px 1px; gap: 1px; background: var(--color-surface);
  border-bottom: 1px solid var(--color-border);
}

.cal-view__bar {
  border-radius: 3px; padding: 1px 3px; font-size: 0.71rem;
  white-space: nowrap; cursor: default;
  display: flex; align-items: center; gap: 3px;
  transition: opacity 0.1s; min-width: 0; min-height: 18px;
  /* background + color set via :style */
  /* #bar: min-height floor so every segment keeps a uniform bar height,
     including edge cases such as a title-less entry where only the icons
     would otherwise set the height. */
}
.cal-view__bar { cursor: grab; }
.cal-view__bar--dragging { opacity: 0.35; }
.cal-view__bar:hover { opacity: 0.85; }
.cal-view__bar-name {
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  min-width: 0; flex: 1; cursor: pointer;
}

/* Action buttons on bars share the chip-action class; extend hover trigger. */
.cal-view__bar:hover .cal-view__chip-action { opacity: 0.7; }

/* ── Day row inside each week ─────────────────────────────────────────────── */
.cal-view__day-row {
  display: grid; grid-template-columns: repeat(7, 1fr);
  background: var(--color-border); gap: 1px;
}

/* ── Day cell ─────────────────────────────────────────────────────────────── */
/* #92: min-height instead of fixed height — cells with many chips or multi-day
        bars can grow rather than clip their content at the border. */
.cal-view__cell {
  background: var(--color-bg);
  min-height: 150px; overflow: hidden;
  display: flex; flex-direction: column; padding: 4px 5px 5px;
}
.cal-view__cell--other-month { background: var(--color-surface); opacity: 0.55; }
.cal-view__cell--weekend { background: color-mix(in srgb, var(--color-bg) 92%, var(--color-accent)); }
.cal-view__cell--today   { background: var(--color-accent-subtle); }
.cal-view__cell--drag-over {
  background: color-mix(in srgb, var(--color-accent-subtle) 60%, var(--color-accent) 20%);
  outline: 2px solid var(--color-accent); outline-offset: -2px;
}

/* ── Cell header ──────────────────────────────────────────────────────────── */
.cal-view__cell-header {
  display: flex; align-items: center; justify-content: space-between;
  min-height: 18px; margin-bottom: 3px;
}
.cal-view__day-number {
  font-size: 0.75rem; font-weight: 500; color: var(--color-text-muted); line-height: 1;
}
.cal-view__day-number--today {
  display: inline-flex; align-items: center; justify-content: center;
  min-width: 19px; height: 19px; border-radius: 50%;
  background: var(--color-accent); color: #fff; font-weight: 700; font-size: 0.72rem; padding: 0 3px;
}
.cal-view__add-btn {
  display: flex; align-items: center; justify-content: center; width: 16px; height: 16px;
  border: none; border-radius: 3px; background: none; color: var(--color-text-muted);
  cursor: pointer; opacity: 0; transition: opacity 0.15s, background 0.1s, color 0.1s; flex-shrink: 0;
}
.cal-view__cell:hover .cal-view__add-btn { opacity: 1; }
.cal-view__add-btn:hover { background: color-mix(in srgb, var(--color-accent-subtle) 80%, transparent); color: var(--color-accent); }

/* ── Chips: scrollable on cell hover ─────────────────────────────────────── */
/* #92: max-height raised from 72px → 90px to match the taller cell. */
.cal-view__chips {
  display: flex; flex-direction: column; gap: 2px;
  overflow-y: hidden; max-height: 90px;
}
.cal-view__cell:hover .cal-view__chips { overflow-y: auto; }
.cal-view__chips::-webkit-scrollbar { width: 3px; }
.cal-view__chips::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.3); border-radius: 2px; }
.cal-view__chips { scrollbar-width: thin; scrollbar-color: rgba(255,255,255,0.25) transparent; }

/* ── Chip ─────────────────────────────────────────────────────────────────── */
.cal-view__chip {
  display: flex; align-items: center; gap: 3px;
  padding: 2px 3px; border-radius: 3px; font-size: 0.72rem;
  flex-shrink: 0; width: 100%;
  /* background + color via :style */
}
.cal-view__chip { cursor: grab; }
.cal-view__chip--dragging { opacity: 0.35; }

.cal-view__chip-recur {
  opacity: 0.65; flex-shrink: 0;
}

.cal-view__chip-icon-btn {
  display: flex; align-items: center; justify-content: center;
  background: none; border: none; cursor: pointer; color: inherit;
  opacity: 0.85; padding: 0; flex-shrink: 0; border-radius: 2px; transition: opacity 0.1s;
}
.cal-view__chip-icon-btn:hover { opacity: 1; }
.cal-view__chip-name {
  flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  cursor: pointer; min-width: 0;
}
.cal-view__chip-time {
  font-size: 0.65rem; opacity: 0.65; flex-shrink: 0;
  font-variant-numeric: tabular-nums; white-space: nowrap;
}
.cal-view__chip-action {
  display: flex; align-items: center; justify-content: center;
  background: none; border: none; cursor: pointer; color: inherit;
  opacity: 0; padding: 1px; border-radius: 2px; flex-shrink: 0; transition: opacity 0.15s;
}
.cal-view__chip:hover .cal-view__chip-action { opacity: 0.7; }
.cal-view__chip-action:hover { opacity: 1 !important; }
.cal-view__chip-action--danger {
  opacity: 1 !important; background: rgba(255,255,255,0.25); border-radius: 2px;
}

/* #81: Week view — taller cells, chips not clipped by max-height ────────── */
.cal-view__weeks--week .cal-view__cell {
  height: auto; min-height: 300px;
}
.cal-view__weeks--week .cal-view__chips {
  max-height: none; overflow-y: auto;
}
.cal-view__weeks--week .cal-view__cell:hover .cal-view__chips { overflow-y: auto; }

/* ── Day view (#81) ──────────────────────────────────────────────────────── */
.cal-view__day-view {
  flex: 1; overflow-y: auto; display: flex; flex-direction: column;
}
.cal-view__dv-header {
  display: flex; align-items: center; gap: 10px; padding: 10px 16px;
  border-bottom: 1px solid var(--color-border); background: var(--color-surface); flex-shrink: 0;
}
.cal-view__dv-header--today { background: var(--color-accent-subtle); }
.cal-view__dv-date-num {
  font-size: 2rem; font-weight: 700; color: var(--color-text-muted); line-height: 1; flex-shrink: 0;
}
.cal-view__dv-date-num--today { color: var(--color-accent); }
.cal-view__dv-date-label {
  font-size: 0.82rem; font-weight: 500; color: var(--color-text-muted); flex: 1; min-width: 0;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.cal-view__dv-add {
  display: flex; align-items: center; gap: 4px; font-size: 0.75rem; color: var(--color-text-muted);
  background: none; border: 1px solid var(--color-border); border-radius: 4px; padding: 4px 8px;
  cursor: pointer; transition: background 0.1s, color 0.1s; flex-shrink: 0;
}
.cal-view__dv-add:hover { background: var(--color-hover); color: var(--color-text); }
.cal-view__dv-empty {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 8px; padding: 64px 32px; color: var(--color-text-muted); flex: 1;
}
.cal-view__dv-empty p { font-size: 0.85rem; margin: 0; }
.cal-view__dv-entry {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 16px 8px 14px; transition: background 0.1s;
  border-bottom: 1px solid var(--color-border);
}
.cal-view__dv-entry:last-child { border-bottom: none; }
.cal-view__dv-entry:hover { background: var(--color-hover); }
.cal-view__dv-stripe { width: 3px; height: 20px; border-radius: 2px; flex-shrink: 0; }
.cal-view__dv-time {
  font-size: 0.72rem; font-weight: 600; color: var(--color-text-muted);
  width: 90px; flex-shrink: 0; font-variant-numeric: tabular-nums;
}
.cal-view__dv-time--allday { color: var(--color-accent); font-weight: 500; }
.cal-view__dv-icon { color: var(--color-text-muted); flex-shrink: 0; }
.cal-view__dv-name {
  flex: 1; font-size: 0.85rem; color: var(--color-text);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  cursor: pointer; min-width: 0;
}
.cal-view__dv-name:hover { text-decoration: underline; }
.cal-view__dv-action {
  display: flex; align-items: center; justify-content: center;
  width: 24px; height: 24px; background: none; border: none;
  cursor: pointer; color: var(--color-text-muted); border-radius: 4px;
  opacity: 0; transition: opacity 0.15s, color 0.15s, background 0.15s; flex-shrink: 0;
}
.cal-view__dv-entry:hover .cal-view__dv-action { opacity: 1; }
.cal-view__dv-action:hover { background: var(--color-hover); color: var(--color-text); }
.cal-view__dv-action--danger {
  opacity: 1 !important; background: rgba(224, 85, 85, 0.12); color: #e05555 !important;
}
</style>

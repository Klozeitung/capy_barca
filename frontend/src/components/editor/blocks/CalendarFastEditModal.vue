<script setup lang="ts">
/**
 * CalendarFastEditModal
 *
 * Compact modal for quickly editing a database entry from within CalendarView.
 *
 * Editable fields
 * ---------------
 * - Entry name (inline text input)
 * - Page icon (via IconPicker, teleported to body)
 * - Chip color (view-local, stored in view.calendarChipColors)
 * - Date property: all-day toggle, start date/time, end date/time
 *
 * Validation
 * ----------
 * End must not be before start. Save is blocked when no start date is set.
 *
 * Emits
 * -----
 * close          – user dismissed the modal
 * update-view    – chip color changed; parent must persist the updated view
 * refresh        – entry data was mutated; parent must re-query entries
 *
 * Changes
 * -------
 * #50  Date and time are separate inputs. The date field is the shared
 *      DatePicker component (custom picker, 1..9999 year range); a time field
 *      (type="time") appears next to it when the all-day toggle is OFF,
 *      defaulting to 00:00 so the user can leave it empty and still get a valid
 *      timed entry (backend receives T00:00). Toggling all-day on/off no longer
 *      needs to reformat the stored string; it merely shows/hides the time
 *      field. The backend always stores start/end as full ISO strings (or
 *      date-only when all-day).
 */
import { ref, computed } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { useBlockStore } from '@/stores/blocks'
import { useDatabaseStore, type DatabaseEntry, type DatabaseView, type PropertySchema } from '@/stores/database'
import IconPicker from '@/components/IconPicker.vue'
import DatePicker from '@/components/DatePicker.vue'
import { CHIP_COLORS, DEFAULT_CHIP_COLOR } from '@/composables/calendarColors'

// ── Props / emits ─────────────────────────────────────────────────────────────

const props = defineProps<{
  entry: DatabaseEntry
  schemas: PropertySchema[]
  view: DatabaseView
  databaseId: string
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'update-view', view: DatabaseView): void
  (e: 'refresh'): void
}>()

// ── Dependencies ──────────────────────────────────────────────────────────────

const { t } = useI18n()
const blockStore = useBlockStore()
const dbStore    = useDatabaseStore()

// ── Entry name ────────────────────────────────────────────────────────────────

const nameDraft = ref(((props.entry.content?.title as string | undefined) ?? '').trim())

// ── Icon picker ───────────────────────────────────────────────────────────────

const showIconPicker  = ref(false)
const iconPickerRect  = ref<DOMRect | null>(null)
const currentIcon     = ref<string | null>(props.entry.icon)

function openIconPicker(e: MouseEvent): void {
  iconPickerRect.value = (e.currentTarget as HTMLElement).getBoundingClientRect()
  showIconPicker.value = true
}

async function onIconUpdate(newIcon: string | null): Promise<void> {
  showIconPicker.value = false
  if (newIcon === currentIcon.value) return
  currentIcon.value = newIcon
  await blockStore.updateAppearance(props.entry.id, { icon: newIcon ?? undefined })
  emit('refresh')
}

// ── Chip color ────────────────────────────────────────────────────────────────

const localColorKey = ref<string>(
  (props.view.calendarChipColors ?? {})[props.entry.id] ?? DEFAULT_CHIP_COLOR,
)

function setColor(key: string): void {
  localColorKey.value = key
  emit('update-view', {
    ...props.view,
    calendarChipColors: {
      ...(props.view.calendarChipColors ?? {}),
      [props.entry.id]: key,
    },
  })
}

// ── Date property ─────────────────────────────────────────────────────────────

const dateSchema = computed<PropertySchema | null>(() => {
  const id = props.view.calendarDateSchemaId
  if (!id) return null
  return props.schemas.find(s => s.id === id) ?? null
})

function getRawDateValue(): Record<string, unknown> {
  const schema = dateSchema.value
  if (!schema) return {}
  return (props.entry.values[schema.id] as Record<string, unknown> | null) ?? {}
}

const rawStart      = (getRawDateValue().start       as string | undefined) ?? ''
const rawEnd        = (getRawDateValue().end         as string | undefined) ?? ''
const rawRepeat        = (getRawDateValue().repeat         as string | undefined) ?? 'none'
const rawRepeatUntil   = (getRawDateValue().repeatUntil    as string | undefined) ?? ''
const rawRepeatInterval = (getRawDateValue().repeatInterval as number | undefined) ?? 1

// #50: Split stored ISO strings into separate date and time parts so the user
// can edit them independently.  A stored value of '2024-03-15T09:30' becomes
// date='2024-03-15', time='09:30'.  A date-only value ('2024-03-15') has an
// empty time part.

function splitIso(s: string): { date: string; time: string } {
  if (!s) return { date: '', time: '' }
  if (s.includes('T')) {
    const [date, rest] = s.split('T')
    return { date, time: rest.slice(0, 5) }
  }
  return { date: s.slice(0, 10), time: '' }
}

const splitStart = splitIso(rawStart)
const splitEnd   = splitIso(rawEnd)

// #75: Repeat / recurrence preference stored in the date value object.
// The frontend stores the intent; expansion of recurring events requires
// backend support and is handled server-side when this field is set.
const localRepeat      = ref<string>(rawRepeat)
/** Interval: "every N [units]". Always ≥ 1. */
const localRepeatInterval = ref<number>(Math.max(1, rawRepeatInterval))
/** Optional ISO date (YYYY-MM-DD) at which the recurrence ends. Empty = open-ended. */
const localRepeatUntil = ref<string>(rawRepeatUntil)

/** Only show interval + until rows when a repeat mode is active. */
const showRepeatUntil = computed(() => localRepeat.value !== 'none')

/** Unit label for the interval row, matches the selected repeat mode. */
const repeatUnitKey = computed(() => {
  switch (localRepeat.value) {
    case 'daily':   return 'db.calendar.repeatUnitDay'
    case 'weekly':  return 'db.calendar.repeatUnitWeek'
    case 'monthly': return 'db.calendar.repeatUnitMonth'
    case 'yearly':  return 'db.calendar.repeatUnitYear'
    default:        return ''
  }
})

/** Clamp until date so it cannot be before the start date. */
function onRepeatUntilChange(val: string): void {
  localRepeatUntil.value = (val && localStartDate.value && val < localStartDate.value)
    ? localStartDate.value
    : val
}

/** Resetting repeat to "none" also clears the until date. */
function onRepeatChange(val: string): void {
  localRepeat.value = val
  if (val === 'none') {
    localRepeatInterval.value = 1
    localRepeatUntil.value    = ''
  }
}

// Per-entry all-day detection: absence of 'T' in the stored start = all-day.
const localAllDay = ref(!rawStart.includes('T'))

// Date parts – always plain YYYY-MM-DD strings.
const localStartDate = ref(splitStart.date)
const localEndDate   = ref(splitEnd.date)

// Time parts – HH:MM strings; default to '00:00' so saving without a time
// still produces a valid timed entry (backend receives T00:00).
const localStartTime = ref(splitStart.time || '00:00')
const localEndTime   = ref(splitEnd.time   || '00:00')

function onAllDayToggle(): void {
  localAllDay.value = !localAllDay.value
  // No format conversion needed – we only show/hide the time inputs.
}

// ── End-before-start: silent clamping ─────────────────────────────────────────

function onStartDateChange(val: string): void {
  localStartDate.value = val
  if (localEndDate.value && val && localEndDate.value < val) {
    localEndDate.value = val
  }
}

function onEndDateChange(val: string): void {
  localEndDate.value = (val && localStartDate.value && val < localStartDate.value)
    ? localStartDate.value
    : val
}

const canSave = computed(() => !!localStartDate.value)

// ── Save ──────────────────────────────────────────────────────────────────────

const isSaving = ref(false)

async function save(): Promise<void> {
  if (!canSave.value || isSaving.value) return
  isSaving.value = true
  try {
    // Name
    const newName = nameDraft.value.trim()
    const oldName = ((props.entry.content?.title as string | undefined) ?? '').trim()
    if (newName !== oldName) {
      await blockStore.updateBlock(props.entry.id, {
        content: { ...(props.entry.content ?? {}), title: newName },
      })
    }

    // Date value
    const schema = dateSchema.value
    if (schema && localStartDate.value) {
      // Build ISO strings: all-day uses date-only; timed appends HH:MM.
      const buildIso = (date: string, time: string) =>
        localAllDay.value ? date : `${date}T${time || '00:00'}`

      let startIso = buildIso(localStartDate.value, localStartTime.value)
      let endIso   = localEndDate.value
        ? buildIso(localEndDate.value, localEndTime.value)
        : startIso

      // Final clamp: ensure end >= start.
      if (endIso < startIso) endIso = startIso

      // Spread the existing raw value first so that fields managed outside
      // this modal (e.g. repeatExceptions set by the recurrence scope dialog)
      // are preserved.  Explicit undefined values below clear their keys in
      // the serialised JSON, which is the correct way to remove them.
      const existingVal = getRawDateValue()
      const isRepeating = localRepeat.value !== 'none'

      await dbStore.upsertValue(props.databaseId, props.entry.id, schema.id, {
        ...existingVal,
        start:           startIso,
        end:             endIso,
        repeat:          isRepeating ? localRepeat.value : undefined,
        repeatInterval:  (isRepeating && localRepeatInterval.value > 1) ? localRepeatInterval.value : undefined,
        repeatUntil:     (isRepeating && localRepeatUntil.value) ? localRepeatUntil.value : undefined,
        // Clear exceptions when repeat is disabled (they'd become orphaned).
        repeatExceptions: isRepeating ? (existingVal.repeatExceptions as string[] | undefined) : undefined,
      })
    } else if (schema && !localStartDate.value) {
      await dbStore.upsertValue(props.databaseId, props.entry.id, schema.id, null)
    }

    emit('refresh')
    emit('close')
  } finally {
    isSaving.value = false
  }
}
</script>

<template>
  <Teleport to="body">
    <!-- ── Backdrop ──────────────────────────────────────────────────────────── -->
    <div class="cfem-backdrop" @mousedown.self="emit('close')">
      <div class="cfem" role="dialog" :aria-label="t('db.calendar.fastEdit')">

        <!-- ── Header: icon + name ───────────────────────────────────────────── -->
        <div class="cfem__header">
          <button
            class="cfem__icon-btn"
            :title="t('main.addIcon')"
            @click.stop="openIconPicker"
          >
            <Icon :icon="currentIcon ?? 'mdi:file-outline'" width="22" height="22" />
          </button>
          <input
            v-model="nameDraft"
            class="cfem__name-input"
            :placeholder="t('main.untitled')"
            @keydown.enter.prevent="save"
            @keydown.escape.prevent="emit('close')"
          />
          <button class="cfem__close" @click="emit('close')">
            <Icon icon="mdi:close" width="15" height="15" />
          </button>
        </div>

        <!-- ── Chip color picker ─────────────────────────────────────────────── -->
        <div class="cfem__section">
          <span class="cfem__label">{{ t('db.calendar.chipColor') }}</span>
          <div class="cfem__color-row">
            <button
              v-for="color in CHIP_COLORS"
              :key="color.key"
              class="cfem__color-swatch"
              :class="{ 'cfem__color-swatch--active': localColorKey === color.key }"
              :style="{ background: color.bg }"
              :title="color.key"
              @click="setColor(color.key)"
            >
              <Icon
                v-if="localColorKey === color.key"
                icon="mdi:check"
                width="11"
                height="11"
                style="color: #fff"
              />
            </button>
          </div>
        </div>

        <!-- ── Date section ──────────────────────────────────────────────────── -->
        <div v-if="dateSchema" class="cfem__section">
          <div class="cfem__date-header">
            <span class="cfem__label">{{ dateSchema.name }}</span>
            <!-- All-day toggle -->
            <button
              class="cfem__allday-toggle"
              :class="{ 'cfem__allday-toggle--on': localAllDay }"
              @click="onAllDayToggle"
            >
              <span class="cfem__allday-track">
                <span class="cfem__allday-thumb" />
              </span>
              {{ t('db.calendar.allDay') }}
            </button>
          </div>

          <!-- Start (#50: separate date + optional time field) -->
          <div class="cfem__date-row">
            <span class="cfem__date-label">{{ t('db.calendar.start') }}</span>
            <DatePicker
              :model-value="localStartDate"
              @update:model-value="onStartDateChange($event)"
            />
            <input
              v-if="!localAllDay"
              type="time"
              class="cfem__time-input"
              :value="localStartTime"
              @change="localStartTime = ($event.target as HTMLInputElement).value"
            />
          </div>

          <!-- End (#50: separate date + optional time field) -->
          <div class="cfem__date-row">
            <span class="cfem__date-label">{{ t('db.calendar.end') }}</span>
            <DatePicker
              :model-value="localEndDate"
              @update:model-value="onEndDateChange($event)"
            />
            <input
              v-if="!localAllDay"
              type="time"
              class="cfem__time-input"
              :value="localEndTime"
              @change="localEndTime = ($event.target as HTMLInputElement).value"
            />
          </div>

          <p v-if="!canSave" class="cfem__error">
            <Icon icon="mdi:alert-circle-outline" width="13" height="13" />
            {{ t('db.calendar.startRequired') }}
          </p>
        </div>

        <div v-else class="cfem__section cfem__no-date">
          {{ t('db.calendar.noDateProperty') }}
        </div>

        <!-- ── Repeat (#75) ──────────────────────────────────────────────────── -->
        <div v-if="dateSchema" class="cfem__section">
          <span class="cfem__label">{{ t('db.calendar.repeat') }}</span>
          <select
            class="cfem__select"
            :value="localRepeat"
            @change="onRepeatChange(($event.target as HTMLSelectElement).value)"
          >
            <option value="none">{{ t('db.calendar.repeatNone') }}</option>
            <option value="daily">{{ t('db.calendar.repeatDaily') }}</option>
            <option value="weekly">{{ t('db.calendar.repeatWeekly') }}</option>
            <option value="monthly">{{ t('db.calendar.repeatMonthly') }}</option>
            <option value="yearly">{{ t('db.calendar.repeatYearly') }}</option>
          </select>

          <!-- Until date: only visible when a repeat mode is active -->
          <div v-if="showRepeatUntil" class="cfem__date-row">
            <span class="cfem__date-label cfem__date-label--until">
              {{ t('db.calendar.repeatEvery') }}
            </span>
            <input
              type="number"
              class="cfem__interval-input"
              :value="localRepeatInterval"
              min="1"
              max="999"
              step="1"
              @change="localRepeatInterval = Math.max(1, parseInt(($event.target as HTMLInputElement).value) || 1)"
            />
            <span class="cfem__interval-unit">{{ t(repeatUnitKey) }}</span>
          </div>

          <!-- Until date: only visible when a repeat mode is active -->
          <div v-if="showRepeatUntil" class="cfem__date-row">
            <span class="cfem__date-label cfem__date-label--until">
              {{ t('db.calendar.repeatUntil') }}
            </span>
            <DatePicker
              :model-value="localRepeatUntil"
              :min-date="localStartDate || ''"
              @update:model-value="onRepeatUntilChange($event)"
            />
            <button
              v-if="localRepeatUntil"
              class="cfem__repeat-clear"
              :title="t('db.calendar.repeatUntilClear')"
              @click="localRepeatUntil = ''"
            >
              <Icon icon="mdi:close-circle-outline" width="15" height="15" />
            </button>
          </div>
        </div>

        <!-- ── Footer ────────────────────────────────────────────────────────── -->
        <div class="cfem__footer">
          <button class="cfem__btn cfem__btn--cancel" @click="emit('close')">
            {{ t('actions.cancel') }}
          </button>
          <button
            class="cfem__btn cfem__btn--save"
            :disabled="!canSave || isSaving"
            @click="save"
          >
            {{ t('actions.save') }}
          </button>
        </div>

      </div>
    </div>

    <!-- ── Icon picker (teleported, positioned at trigger) ───────────────────── -->
    <IconPicker
      v-if="showIconPicker"
      :model-value="currentIcon"
      :trigger-rect="iconPickerRect"
      @update:model-value="onIconUpdate"
      @close="showIconPicker = false"
    />
  </Teleport>
</template>

<style scoped>
/* ── Backdrop ────────────────────────────────────────────────────────────── */
.cfem-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 400;
}

/* ── Dialog ──────────────────────────────────────────────────────────────── */
.cfem {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  width: min(400px, 94vw);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.25);
}

/* ── Header ──────────────────────────────────────────────────────────────── */
.cfem__header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--color-border);
}

.cfem__icon-btn {
  background: none;
  border: none;
  cursor: pointer;
  color: var(--color-text-muted);
  padding: 3px;
  border-radius: 5px;
  display: flex;
  align-items: center;
  flex-shrink: 0;
  transition: background 0.1s, color 0.1s;
}

.cfem__icon-btn:hover {
  background: var(--color-hover);
  color: var(--color-text);
}

.cfem__name-input {
  flex: 1;
  background: transparent;
  border: none;
  outline: none;
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--color-text);
  min-width: 0;
}

.cfem__name-input::placeholder {
  color: var(--color-text-muted);
  font-weight: 400;
}

.cfem__close {
  background: none;
  border: none;
  cursor: pointer;
  color: var(--color-text-muted);
  display: flex;
  align-items: center;
  padding: 3px;
  border-radius: 4px;
  flex-shrink: 0;
  transition: color 0.15s, background 0.15s;
}

.cfem__close:hover {
  color: var(--color-text);
  background: var(--color-hover);
}

/* ── Section ─────────────────────────────────────────────────────────────── */
.cfem__section {
  padding: 12px 14px;
  border-bottom: 1px solid var(--color-border);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.cfem__label {
  font-size: 0.7rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-text-muted);
}

/* ── Color swatches ──────────────────────────────────────────────────────── */
.cfem__color-row {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.cfem__color-swatch {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  border: 2px solid transparent;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: transform 0.1s, border-color 0.1s;
  flex-shrink: 0;
}

.cfem__color-swatch:hover {
  transform: scale(1.15);
}

.cfem__color-swatch--active {
  border-color: var(--color-text);
}

/* ── Date section ────────────────────────────────────────────────────────── */
.cfem__date-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.cfem__allday-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  background: none;
  border: none;
  cursor: pointer;
  font-size: 0.75rem;
  color: var(--color-text-muted);
  transition: color 0.1s;
}

.cfem__allday-toggle:hover { color: var(--color-text); }

.cfem__allday-toggle--on { color: var(--color-accent); }

.cfem__allday-track {
  position: relative;
  display: inline-block;
  width: 28px;
  height: 16px;
  background: var(--color-hover);
  border-radius: 8px;
  transition: background 0.15s;
  flex-shrink: 0;
}

.cfem__allday-toggle--on .cfem__allday-track {
  background: var(--color-accent);
}

.cfem__allday-thumb {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: #fff;
  transition: transform 0.15s;
  box-shadow: 0 1px 3px rgba(0,0,0,0.2);
}

.cfem__allday-toggle--on .cfem__allday-thumb {
  transform: translateX(12px);
}

/* #50: date row now contains up to two inputs (date + time) side by side */
.cfem__date-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.cfem__date-label {
  font-size: 0.78rem;
  color: var(--color-text-muted);
  width: 40px;
  flex-shrink: 0;
}

.cfem__date-input {
  flex: 1;
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 5px;
  padding: 5px 8px;
  font-size: 0.8rem;
  color: var(--color-text);
  min-width: 0;
}

/* #50: time field is narrower so both fit on one row */
.cfem__time-input {
  flex: 0 0 auto;
  width: 88px;
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 5px;
  padding: 5px 6px;
  font-size: 0.8rem;
  color: var(--color-text);
}

.cfem__date-input--error {
  border-color: #e05555;
  outline: 1px solid #e05555;
}

.cfem__error {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 0.75rem;
  color: #e05555;
  margin: 0;
}

.cfem__no-date {
  font-size: 0.82rem;
  color: var(--color-text-muted);
  font-style: italic;
}

/* ── Repeat select (#75) ─────────────────────────────────────────────────── */
.cfem__select {
  width: 100%;
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 5px;
  padding: 5px 8px;
  font-size: 0.8rem;
  color: var(--color-text);
  cursor: pointer;
  appearance: auto;
}

/* "Until" / "Every" label is slightly wider than start/end labels */
.cfem__date-label--until {
  width: 56px;
}

.cfem__interval-input {
  width: 56px;
  flex-shrink: 0;
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 5px;
  padding: 5px 6px;
  font-size: 0.8rem;
  color: var(--color-text);
  text-align: center;
}

/* hide browser spin arrows; keeps it compact */
.cfem__interval-input::-webkit-inner-spin-button,
.cfem__interval-input::-webkit-outer-spin-button { -webkit-appearance: none; }
.cfem__interval-input { -moz-appearance: textfield; }

.cfem__interval-unit {
  font-size: 0.8rem;
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.cfem__repeat-clear {
  display: flex;
  align-items: center;
  justify-content: center;
  background: none;
  border: none;
  cursor: pointer;
  color: var(--color-text-muted);
  padding: 2px;
  border-radius: 3px;
  flex-shrink: 0;
  transition: color 0.1s, background 0.1s;
}

.cfem__repeat-clear:hover {
  color: var(--color-text);
  background: var(--color-hover);
}

/* ── Footer ──────────────────────────────────────────────────────────────── */
.cfem__footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 10px 14px;
}

.cfem__btn {
  padding: 6px 14px;
  border-radius: 5px;
  border: none;
  font-size: 0.82rem;
  cursor: pointer;
  transition: background 0.1s, opacity 0.1s;
}

.cfem__btn--cancel {
  background: var(--color-hover);
  color: var(--color-text-muted);
}

.cfem__btn--cancel:hover { background: var(--color-border); }

.cfem__btn--save {
  background: var(--color-accent);
  color: #fff;
}

.cfem__btn--save:hover:not(:disabled) { opacity: 0.85; }

.cfem__btn--save:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
</style>

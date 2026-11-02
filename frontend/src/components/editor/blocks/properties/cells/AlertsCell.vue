<script setup lang="ts">
/**
 * AlertsCell
 *
 * Cell of an ``alerts`` property: the user's own reminders on an entry.
 *
 * The cell shows the next alert still to come and how many more follow.
 * Clicking it opens an editor with every alert of the user on this entry,
 * each with its date and time and an optional title, and a row for a new one.
 * Other users' alerts are never part of the value, so nothing of theirs is
 * shown or written here.
 *
 * Read access to the entry is enough to keep alerts on it, so the editor
 * opens regardless of ``useEditable``.
 *
 * Every change is sent at once through the alerts store, one alert at a
 * time. Times are entered in local time and sent as UTC; a time that has
 * already passed is refused by the server and reported in the editor.
 *
 * In a database with a calendar, every entry also gets an alert before its
 * date from the server; those are not part of the value, and an info icon
 * in the editor header says so.
 *
 * Value shape: { alerts: AlertItem[] } (see ``composables/alertValue``).
 */
import { computed, ref } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { ApiError } from '@/api/client'
import { useDatabaseStore, type DatabaseEntry, type PropertySchema } from '@/stores/database'
import { useAlertsStore } from '@/stores/alerts'
import { isCalendarDateSchema } from '@/stores/propertyTypes'
import { useEscapeKey } from '@/composables/useEscapeStack'
import {
  ALERT_TITLE_MAX_LENGTH,
  isInFuture,
  isUpcoming,
  localToUtcIso,
  nextAlert,
  parseAlerts,
  utcToLocalIso,
  type AlertItem,
} from '@/composables/alertValue'
import { formatAlertTime, resolveDateFormat } from './cellUtils'
import DatePicker from '@/components/DatePicker.vue'

const props = defineProps<{
  entry: DatabaseEntry
  schema: PropertySchema
  databaseId: string
}>()

const { t } = useI18n()
const alertsStore = useAlertsStore()
const databaseStore = useDatabaseStore()

// ── Value ─────────────────────────────────────────────────────────────────────

const alerts = computed<AlertItem[]>(() => parseAlerts(props.entry.values[props.schema.id]))
const upcoming = computed(() => alerts.value.filter(isUpcoming))
const next = computed(() => nextAlert(alerts.value))
const dateFormat = computed(() => resolveDateFormat(props.schema))

/** Whether the database has a calendar, whose dates get alerts of their own. */
const hasCalendar = computed(() =>
  databaseStore.getSchemas(props.databaseId).some(isCalendarDateSchema))

function timeLabel(alert: AlertItem): string {
  return formatAlertTime(alert.at, props.schema)
}

// ── Editor ────────────────────────────────────────────────────────────────────

const open = ref(false)
const busy = ref(false)
const error = ref('')
const newAt = ref('')
const newTitle = ref('')

function openEditor(): void {
  error.value = ''
  open.value = true
}

function closeEditor(): void {
  open.value = false
  newAt.value = ''
  newTitle.value = ''
  error.value = ''
}

useEscapeKey(closeEditor, open)

function messageFor(err: unknown): string {
  if (err instanceof ApiError && err.status === 422) return t('alerts.errors.invalid')
  if (err instanceof ApiError && (err.status === 403 || err.status === 404)) return t('alerts.errors.noAccess')
  return t('alerts.errors.saveFailed')
}

/** Run one write; reports a refusal in the editor. */
async function run(write: () => Promise<unknown>): Promise<boolean> {
  busy.value = true
  error.value = ''
  try {
    await write()
    return true
  } catch (err) {
    error.value = messageFor(err)
    return false
  } finally {
    busy.value = false
  }
}

async function addAlert(): Promise<void> {
  if (!newAt.value) return
  if (!isInFuture(newAt.value)) {
    error.value = t('alerts.errors.past')
    return
  }
  const done = await run(() => alertsStore.createAlert(
    props.databaseId, props.entry.id, localToUtcIso(newAt.value), newTitle.value.trim(), alerts.value,
  ))
  if (done) {
    newAt.value = ''
    newTitle.value = ''
  }
}

async function changeTime(alert: AlertItem, local: string): Promise<void> {
  if (!local || local === utcToLocalIso(alert.at)) return
  if (!isInFuture(local)) {
    error.value = t('alerts.errors.past')
    return
  }
  await run(() => alertsStore.updateAlert(
    props.databaseId, alert, { at: localToUtcIso(local) }, alerts.value,
  ))
}

async function changeTitle(alert: AlertItem, event: Event): Promise<void> {
  const title = (event.target as HTMLInputElement).value.trim()
  if (title === alert.title) return
  await run(() => alertsStore.updateAlert(props.databaseId, alert, { title }, alerts.value))
}

async function removeAlert(alert: AlertItem): Promise<void> {
  await run(() => alertsStore.deleteAlert(props.databaseId, alert, alerts.value))
}
</script>

<template>
  <span class="db__cell-value alerts-cell" @click.stop="openEditor">
    <Icon
      :icon="next ? 'mdi:bell-outline' : 'mdi:bell-off-outline'"
      width="13"
      height="13"
      class="alerts-cell__icon"
      :class="{ 'alerts-cell__icon--idle': !next }"
    />
    <span v-if="next" class="db__cell-text">
      {{ timeLabel(next) }}<template v-if="next.title"> · {{ next.title }}</template>
    </span>
    <span v-if="upcoming.length > 1" class="alerts-cell__more">+{{ upcoming.length - 1 }}</span>
  </span>

  <Teleport v-if="open" to="body">
    <div class="alerts-editor__backdrop" @click="closeEditor"></div>
    <div class="alerts-editor" role="dialog" :aria-label="t('alerts.editorTitle')" @click.stop>
      <div class="alerts-editor__header">
        <Icon icon="mdi:bell-outline" width="14" height="14" class="alerts-editor__header-icon" />
        <span class="alerts-editor__title">{{ t('alerts.editorTitle') }}</span>
        <Icon
          v-if="hasCalendar"
          icon="mdi:information-outline"
          width="14"
          height="14"
          class="alerts-editor__info"
          role="img"
          tabindex="0"
          :title="t('alerts.calendarHint')"
          :aria-label="t('alerts.calendarHint')"
        />
        <button class="alerts-editor__close" :title="t('actions.close')" @click="closeEditor">
          <Icon icon="mdi:close" width="14" height="14" />
        </button>
      </div>

      <div class="alerts-editor__body">
        <p class="alerts-editor__hint">{{ t('alerts.privateHint') }}</p>

        <p v-if="alerts.length === 0" class="alerts-editor__empty">{{ t('alerts.empty') }}</p>

        <div
          v-for="alert in alerts"
          :key="alert.id"
          class="alerts-editor__row"
          :class="{ 'alerts-editor__row--fired': !isUpcoming(alert) }"
        >
          <Icon
            :icon="isUpcoming(alert) ? 'mdi:bell-outline' : 'mdi:bell-check-outline'"
            width="14"
            height="14"
            class="alerts-editor__row-icon"
            :title="isUpcoming(alert) ? undefined : t('alerts.fired')"
          />
          <DatePicker
            class="alerts-editor__time"
            :model-value="utcToLocalIso(alert.at)"
            :include-time="true"
            :clearable="false"
            :format="dateFormat"
            :disabled="busy"
            @update:model-value="changeTime(alert, $event)"
          />
          <input
            class="alerts-editor__input"
            :value="alert.title"
            :maxlength="ALERT_TITLE_MAX_LENGTH"
            :placeholder="t('alerts.titlePlaceholder')"
            :disabled="busy"
            @blur="changeTitle(alert, $event)"
            @keydown.enter.prevent="($event.target as HTMLInputElement).blur()"
          />
          <button
            class="alerts-editor__remove"
            :title="t('alerts.remove')"
            :disabled="busy"
            @click="removeAlert(alert)"
          >
            <Icon icon="mdi:trash-can-outline" width="13" height="13" />
          </button>
        </div>

        <div class="alerts-editor__row alerts-editor__row--new">
          <Icon icon="mdi:bell-plus-outline" width="14" height="14" class="alerts-editor__row-icon" />
          <DatePicker
            v-model="newAt"
            class="alerts-editor__time"
            :include-time="true"
            :format="dateFormat"
            :placeholder="t('alerts.timePlaceholder')"
            :disabled="busy"
          />
          <input
            v-model="newTitle"
            class="alerts-editor__input"
            :maxlength="ALERT_TITLE_MAX_LENGTH"
            :placeholder="t('alerts.titlePlaceholder')"
            :disabled="busy"
            @keydown.enter.prevent="addAlert"
          />
          <button
            class="alerts-editor__add"
            :disabled="busy || !newAt"
            @click="addAlert"
          >
            {{ t('alerts.add') }}
          </button>
        </div>

        <p v-if="error" class="alerts-editor__error" role="alert">{{ error }}</p>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.alerts-cell {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 7px 12px;
  font-size: 0.875rem;
  color: var(--color-text);
  min-height: 36px;
  overflow: hidden;
  white-space: nowrap;
  cursor: pointer;
}

.alerts-cell__icon {
  flex-shrink: 0;
  color: var(--color-accent);
}

.alerts-cell__icon--idle {
  color: var(--color-text-muted);
  opacity: 0.5;
}

.alerts-editor__info {
  flex-shrink: 0;
  color: var(--color-text-muted);
  cursor: help;
}

.alerts-cell__more {
  flex-shrink: 0;
  font-size: 0.72rem;
  color: var(--color-text-muted);
}

.alerts-editor__backdrop {
  position: fixed;
  inset: 0;
  /* Above the SideView panel (z-index 900), like the timeline editor. */
  z-index: 1000;
  background: rgba(0, 0, 0, 0.35);
}

.alerts-editor {
  position: fixed;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  z-index: 1001;
  width: 520px;
  max-width: calc(100vw - 32px);
  max-height: min(520px, calc(100vh - 32px));
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.alerts-editor__header {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.alerts-editor__header-icon {
  color: var(--color-text-muted);
}

.alerts-editor__title {
  flex: 1;
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.alerts-editor__close {
  background: none;
  border: none;
  cursor: pointer;
  color: var(--color-text-muted);
  padding: 2px;
  border-radius: 3px;
  display: flex;
}

.alerts-editor__close:hover {
  color: var(--color-text);
  background: var(--color-hover);
}

.alerts-editor__body {
  flex: 1;
  overflow-y: auto;
  padding: 10px 12px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.alerts-editor__hint,
.alerts-editor__empty {
  margin: 0;
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

.alerts-editor__row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.alerts-editor__row--fired {
  opacity: 0.6;
}

.alerts-editor__row--new {
  padding-top: 8px;
  border-top: 1px solid var(--color-border);
}

.alerts-editor__row-icon {
  flex-shrink: 0;
  color: var(--color-text-muted);
}

.alerts-editor__time {
  flex: 0 0 10rem;
  min-width: 9.5rem;
}

.alerts-editor__input {
  flex: 1;
  min-width: 0;
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 4px;
  padding: 4px 6px;
  font-size: 0.78rem;
  color: var(--color-text);
  outline: none;
}

.alerts-editor__input:focus {
  border-color: var(--color-accent);
}

.alerts-editor__remove {
  flex-shrink: 0;
  display: flex;
  background: none;
  border: none;
  border-radius: 3px;
  padding: 3px;
  cursor: pointer;
  color: var(--color-text-muted);
}

.alerts-editor__remove:hover:not(:disabled) {
  color: #e05555;
  background: var(--color-hover);
}

.alerts-editor__add {
  flex-shrink: 0;
  background: var(--color-accent);
  color: #fff;
  border: none;
  border-radius: 4px;
  padding: 4px 10px;
  font-size: 0.78rem;
  cursor: pointer;
}

.alerts-editor__add:disabled {
  opacity: 0.5;
  cursor: default;
}

.alerts-editor__error {
  margin: 0;
  font-size: 0.75rem;
  color: #e05555;
}
</style>

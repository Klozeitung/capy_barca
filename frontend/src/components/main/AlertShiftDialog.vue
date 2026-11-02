<script setup lang="ts">
/**
 * AlertShiftDialog
 *
 * Asks whether the user's own alerts on a calendar entry move along with
 * its date, after the date was moved (``CALENDAR_DATE_MOVED_EVENT`` from the
 * database store):
 *
 * * Keep: the alerts stay where they are.
 * * Shift by day: each alert keeps its time of day and its distance in
 *   days, so "two days before at 08:00" stays that, relative to the new date.
 * * Shift exactly: each alert keeps its exact distance to the date.
 *
 * Several moves arriving while the dialog is open are asked one after
 * another; repeated moves of the same entry are merged into one question,
 * from the first start to the last.
 */
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { useAlertsStore } from '@/stores/alerts'
import { useEscapeKey } from '@/composables/useEscapeStack'
import {
  CALENDAR_DATE_MOVED_EVENT,
  type AlertShiftMode,
  type CalendarDateMove,
} from '@/composables/alertValue'

const { t } = useI18n()
const alertsStore = useAlertsStore()

const queue = ref<CalendarDateMove[]>([])
const busy = ref(false)
const failed = ref(false)

function onMoved(e: Event): void {
  const move = (e as CustomEvent<CalendarDateMove>).detail
  // The question being answered right now is not merged into, so a shift
  // already on its way does not swallow the newer move.
  const same = queue.value.find((m, i) => m.entryId === move.entryId && !(busy.value && i === 0))
  if (same) same.newStart = move.newStart
  else queue.value.push({ ...move })
}

onMounted(() => window.addEventListener(CALENDAR_DATE_MOVED_EVENT, onMoved))
onBeforeUnmount(() => window.removeEventListener(CALENDAR_DATE_MOVED_EVENT, onMoved))

const open = computed(() => queue.value.length > 0)
useEscapeKey(() => choose('keep'), open)

async function choose(mode: AlertShiftMode): Promise<void> {
  const move = queue.value[0]
  if (!move || busy.value) return
  if (mode !== 'keep') {
    busy.value = true
    failed.value = false
    try {
      await alertsStore.shiftAlerts(move, mode)
    } catch {
      failed.value = true
      return
    } finally {
      busy.value = false
    }
  }
  failed.value = false
  queue.value.shift()
}

</script>

<template>
  <Teleport to="body">
    <template v-if="open">
      <div class="asd__backdrop"></div>
      <div class="asd" role="alertdialog" aria-modal="true" :aria-label="t('alerts.shift.title')">
        <div class="asd__header">
          <Icon icon="mdi:bell-cog-outline" width="16" height="16" class="asd__icon" />
          <span class="asd__title">{{ t('alerts.shift.title') }}</span>
        </div>
        <p class="asd__text">{{ t('alerts.shift.text') }}</p>
        <div class="asd__options">
          <button class="asd__option" :disabled="busy" @click="choose('keep')">
            <span class="asd__option-label">{{ t('alerts.shift.keep') }}</span>
            <span class="asd__option-hint">{{ t('alerts.shift.keepHint') }}</span>
          </button>
          <button class="asd__option" :disabled="busy" @click="choose('day')">
            <span class="asd__option-label">{{ t('alerts.shift.day') }}</span>
            <span class="asd__option-hint">{{ t('alerts.shift.dayHint') }}</span>
          </button>
          <button class="asd__option" :disabled="busy" @click="choose('exact')">
            <span class="asd__option-label">{{ t('alerts.shift.exact') }}</span>
            <span class="asd__option-hint">{{ t('alerts.shift.exactHint') }}</span>
          </button>
        </div>
        <p v-if="failed" class="asd__error" role="alert">{{ t('alerts.shift.failed') }}</p>
      </div>
    </template>
  </Teleport>
</template>

<style scoped>
.asd__backdrop {
  position: fixed;
  inset: 0;
  z-index: 1100;
  background: rgba(0, 0, 0, 0.35);
}

.asd {
  position: fixed;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  z-index: 1101;
  width: 420px;
  max-width: calc(100vw - 32px);
  padding: 14px 16px 16px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.asd__header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.asd__icon {
  color: var(--color-accent);
}

.asd__title {
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--color-text);
}

.asd__text {
  margin: 0;
  font-size: 0.8rem;
  color: var(--color-text-muted);
}

.asd__options {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.asd__option {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  padding: 8px 10px;
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 6px;
  cursor: pointer;
  text-align: left;
  color: var(--color-text);
}

.asd__option:hover:not(:disabled) {
  border-color: var(--color-accent);
}

.asd__option:disabled {
  opacity: 0.6;
  cursor: progress;
}

.asd__option-label {
  font-size: 0.82rem;
  font-weight: 600;
}

.asd__option-hint {
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

.asd__error {
  margin: 0;
  font-size: 0.75rem;
  color: #e05555;
}
</style>

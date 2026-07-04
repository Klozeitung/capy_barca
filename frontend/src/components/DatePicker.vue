<script setup lang="ts">
/**
 * DatePicker
 *
 * Thin wrapper around @vuepic/vue-datepicker that speaks the application's
 * canonical ISO string contract instead of Date objects, and supports the full
 * 1..9999 year range.
 *
 * Why a custom picker: the native ``<input type="date">`` widget on some
 * platforms - notably Firefox on Android - is hard-capped to the years
 * 1900..2100 by the operating system. A JS-rendered picker has no such bound,
 * which matters for a world-building workspace whose dates span antiquity and
 * the far future.
 *
 * A single value is modelled. Callers compose start/end ranges from two
 * instances, matching the existing separate-field layout in the cells, filter
 * rows, and calendar editor.
 *
 * Contract:
 *   modelValue - canonical ISO string ('' when empty):
 *                  includeTime === false  ->  "YYYY-MM-DD"
 *                  includeTime === true   ->  "YYYY-MM-DDTHH:mm"
 *
 * All conversions run through ``composables/dateValue``, which hardens the
 * Date <-> ISO round-trip for years 1..99 (the JS Date 0..99 -> 1900s trap) and
 * always emits a zero-padded four-digit year so lexicographic start/end
 * comparisons keep working.
 *
 * UI: an inline text-entry field (type a date directly - the fast path for
 * far-off years) with a calendar icon that opens the popover. Month and weekday
 * names follow the active i18n locale rather than the OS.
 */
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { VueDatePicker } from '@vuepic/vue-datepicker'
import { enUS, de } from 'date-fns/locale'
import type { Locale } from 'date-fns'
import { parseIsoToDate, formatDateToIso } from '@/composables/dateValue'

const props = withDefaults(defineProps<{
  modelValue: string
  /** Show a time selector and emit "YYYY-MM-DDTHH:mm" instead of a plain date. */
  includeTime?: boolean
  /** Allow clearing the value back to ''. */
  clearable?: boolean
  placeholder?: string
  /** Disable interaction (read-only display). */
  disabled?: boolean
}>(), {
  includeTime: false,
  clearable: true,
  placeholder: '',
  disabled: false,
})

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
}>()

const { locale } = useI18n()

// vue-datepicker's ``locale`` prop expects a date-fns Locale object (v14+),
// not a BCP-47 string, so the active i18n locale code is mapped to one. English
// is the fallback for any non-German locale.
const dpLocale = computed<Locale>(() => (locale.value.startsWith('de') ? de : enUS))

// vue-datepicker binds a Date; convert to/from the app's ISO string contract.
// The getter/setter is the single conversion boundary, so every consumer keeps
// working purely with ISO strings.
const dateModel = computed<Date | null>({
  get: () => parseIsoToDate(props.modelValue),
  set: (d) => emit('update:modelValue', formatDateToIso(d, props.includeTime)),
})

// Display and text-entry format follow includeTime. Four-digit year (yyyy)
// keeps low years unambiguous both on screen and when typed.
const displayFormat = computed(() => (props.includeTime ? 'yyyy-MM-dd HH:mm' : 'yyyy-MM-dd'))
</script>

<template>
  <VueDatePicker
    v-model="dateModel"
    :locale="dpLocale"
    :year-range="[1, 9999]"
    :enable-time-picker="includeTime"
    :format="displayFormat"
    :preview-format="displayFormat"
    :clearable="clearable"
    :placeholder="placeholder"
    :disabled="disabled"
    text-input
    auto-apply
    :teleport="true"
    input-class-name="dp-app-input"
  />
</template>

<!--
  Non-scoped so the theme variables also reach the popover menu, which
  vue-datepicker teleports to <body>. The application's design tokens are
  mapped onto vue-datepicker's --dp-* variables.
-->
<style>
.dp__theme_light,
.dp__theme_dark {
  --dp-background-color: var(--color-surface);
  --dp-text-color: var(--color-text);
  --dp-primary-color: var(--color-accent);
  --dp-primary-text-color: #fff;
  --dp-border-color: var(--color-border);
  --dp-border-color-hover: var(--color-accent);
  --dp-hover-color: var(--color-hover);
  --dp-menu-border-color: var(--color-border);
  --dp-icon-color: var(--color-text-muted);
}

.dp-app-input {
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 4px;
  padding: 4px 6px;
  font-size: 0.78rem;
  color: var(--color-text);
  outline: none;
  transition: border-color 0.12s;
}
.dp-app-input:focus {
  border-color: var(--color-accent);
}
</style>

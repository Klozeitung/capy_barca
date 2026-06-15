<script setup lang="ts">
/**
 * FormulaCell
 *
 * Displays the evaluated result of a formula expression. Formulas are
 * evaluated by the backend on every dependent value change; this cell is
 * always read-only.
 *
 * Value shape:
 *   { result: string | number | boolean | null, error?: string, style?: string[] }
 *
 * Config shape (set in PropertySettingsModal):
 *   { expression: string }
 *
 * Result rendering
 * ----------------
 * The backend stores all formula results as JSON scalars. Datetime results
 * are serialised to ISO 8601 strings before storage. This component detects
 * the result type and renders accordingly:
 *
 *   boolean  → check / cross icon
 *   ISO date / datetime → rendered in the user's preferred date format,
 *                         with time shown only when present and non-midnight
 *   number   → toLocaleString (decimal comma for de-DE locale)
 *   string   → as-is
 *   null     → empty
 *
 * Style hints (from style() formulas)
 * ------------------------------------
 * When the backend returns a ``style`` array, the cell applies the
 * corresponding CSS. Supported hints match the Notion style() function:
 *
 *   Text formatting : "b" (bold)  "i" (italic)  "u" (underline)
 *                     "s" (strikethrough)  "c" (code / monospace)
 *   Text colors     : "gray" "brown" "orange" "yellow" "green"
 *                     "blue" "purple" "pink" "red"
 *   Background      : same color names + "_background" suffix
 *                     e.g. "red_background"
 *
 * Error state: when the backend returns an error the cell shows an error
 * indicator. The full error message is accessible via a native tooltip
 * (title attribute) so users can hover to diagnose configuration issues.
 */
import { computed } from 'vue'
import { Icon } from '@iconify/vue'
import type { DatabaseEntry, PropertySchema } from '@/stores/database'
import { useAuthStore } from '@/stores/auth'
import { getCellValue, formatCanonicalDate, ISO_DATE_RE, ISO_DATETIME_RE } from './cellUtils'

const props = defineProps<{
  entry: DatabaseEntry
  schema: PropertySchema
}>()

const auth = useAuthStore()

// ── ISO datetime detection ────────────────────────────────────────────────────
//
// Detection regexes and the canonical-date formatter are shared via cellUtils
// so formula dates render in the user's preferred format (#10), consistent
// with date and rollup cells. Time is shown only when present and non-midnight.

// ── Style hint maps ───────────────────────────────────────────────────────────

const STYLE_TEXT_COLORS: Record<string, string> = {
  gray:   '#9b9a97',
  brown:  '#64473a',
  orange: '#d9730d',
  yellow: '#dfab01',
  green:  '#0f7b6c',
  blue:   '#0b6e99',
  purple: '#6940a5',
  pink:   '#ad1a72',
  red:    '#e03e3e',
}

const STYLE_BG_COLORS: Record<string, string> = {
  gray:   '#ebeced',
  brown:  '#e9e5e3',
  orange: '#faebdd',
  yellow: '#fef3db',
  green:  '#ddedea',
  blue:   '#ddebf1',
  purple: '#eae4f2',
  pink:   '#f4dfeb',
  red:    '#fbe4e4',
}

// ── Cell data ─────────────────────────────────────────────────────────────────

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const cellData = computed(() => getCellValue(props.entry, props.schema.id) as any)
const hasError  = computed(() => !!cellData.value?.error)
const errorMessage = computed(() => cellData.value?.error ?? '')

type ResultKind = 'empty' | 'boolean' | 'datetime' | 'date' | 'number' | 'string'

const resultKind = computed<ResultKind>(() => {
  if (cellData.value === null || cellData.value === undefined) return 'empty'
  const r = cellData.value.result
  if (r === null || r === undefined) return 'empty'
  if (typeof r === 'boolean') return 'boolean'
  if (typeof r === 'number') return 'number'
  if (typeof r === 'string') {
    if (ISO_DATETIME_RE.test(r)) return 'datetime'
    if (ISO_DATE_RE.test(r))     return 'date'
  }
  return 'string'
})

const resultValue = computed(() => cellData.value?.result)

const displayText = computed<string>(() => {
  const r = resultValue.value
  switch (resultKind.value) {
    case 'empty':    return ''
    case 'boolean':  return ''                      // rendered via icon
    case 'datetime': return formatCanonicalDate(r as string, auth.dateFormat)
    case 'date':     return formatCanonicalDate(r as string, auth.dateFormat)
    case 'number':   return (r as number).toLocaleString('de-DE', { maximumFractionDigits: 10 })
    case 'string':   return r as string
    default:         return String(r)
  }
})

// ── Style hint rendering ──────────────────────────────────────────────────────

const formulaInlineStyle = computed<Record<string, string>>(() => {
  const hints = (cellData.value?.style ?? []) as string[]
  if (!hints.length) return {}

  const css: Record<string, string> = {}
  const textDecorations: string[] = []

  for (const hint of hints) {
    if (hint.endsWith('_background')) {
      const colorKey = hint.slice(0, -'_background'.length)
      const bg = STYLE_BG_COLORS[colorKey]
      if (bg) css['backgroundColor'] = bg
    } else if (STYLE_TEXT_COLORS[hint]) {
      css['color'] = STYLE_TEXT_COLORS[hint]
    } else if (hint === 'b') {
      css['fontWeight'] = 'bold'
    } else if (hint === 'i') {
      css['fontStyle'] = 'italic'
    } else if (hint === 'u') {
      textDecorations.push('underline')
    } else if (hint === 's') {
      textDecorations.push('line-through')
    } else if (hint === 'c') {
      css['fontFamily'] = 'monospace'
    }
  }

  if (textDecorations.length) {
    css['textDecoration'] = textDecorations.join(' ')
  }

  return css
})
</script>

<template>
  <span
    class="formula-cell"
    :class="{
      'formula-cell--error':   hasError,
      'formula-cell--boolean': !hasError && resultKind === 'boolean',
      'formula-cell--true':    !hasError && resultKind === 'boolean' && resultValue === true,
      'formula-cell--false':   !hasError && resultKind === 'boolean' && resultValue === false,
    }"
    :style="hasError ? undefined : formulaInlineStyle"
    :title="hasError ? errorMessage : undefined"
  >
    <!-- Error state -->
    <template v-if="hasError">
      <Icon icon="mdi:alert-circle-outline" width="13" height="13" class="formula-cell__error-icon" />
      <span class="formula-cell__error-text" :title="errorMessage">Error</span>
    </template>

    <!-- Boolean: icon only -->
    <template v-else-if="resultKind === 'boolean'">
      <Icon
        :icon="resultValue ? 'mdi:check-circle' : 'mdi:close-circle-outline'"
        width="15" height="15"
      />
    </template>

    <!-- Datetime / date: calendar icon + formatted string -->
    <template v-else-if="resultKind === 'datetime' || resultKind === 'date'">
      <Icon icon="mdi:calendar-outline" width="13" height="13" class="formula-cell__date-icon" />
      {{ displayText }}
    </template>

    <!-- Number / string: plain text -->
    <template v-else>
      {{ displayText }}
    </template>
  </span>
</template>

<style scoped>
.formula-cell {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 7px 12px;
  font-size: 0.875rem;
  min-height: 36px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--color-text-muted);
  cursor: default;
  font-variant-numeric: tabular-nums;
}

.formula-cell--error {
  color: #e05555;
  cursor: help;
}

.formula-cell__error-icon {
  flex-shrink: 0;
  color: #e05555;
}

.formula-cell__error-text {
  font-style: italic;
  font-size: 0.8rem;
}

.formula-cell__date-icon {
  flex-shrink: 0;
  opacity: 0.5;
}

/* Boolean states */
.formula-cell--true  { color: #3dba76; }
.formula-cell--false { color: var(--color-text-muted); opacity: 0.5; }
</style>


<script setup lang="ts">
/**
 * RollupCell
 *
 * Displays the result of a rollup aggregation over a related database column.
 * This is a read-only computed value; the aggregation is performed by the
 * backend and stored as the cell value.
 *
 * Value shape:
 *   { result: number | string | boolean | null | any[] | Record<string,number>,
 *     function: string, error?: string }
 *
 * Result variants by function family:
 *   Scalar   – count*, percent_*, sum, avg, median, min, max, range,
 *              first_value, last_value
 *   List     – show_original   → result: any[]
 *   Dict     – percent_per_option → result: Record<string, number>
 *
 * Layout: [function badge] [value / chips]
 */
import { computed, ref } from 'vue'
import { Icon } from '@iconify/vue'
import type { DatabaseEntry, PropertySchema } from '@/stores/database'
import { useAuthStore } from '@/stores/auth'
import { getCellValue, maybeFormatRollupDate } from './cellUtils'
import SideView from '@/components/main/SideView.vue'

const props = defineProps<{
  entry: DatabaseEntry
  schema: PropertySchema
}>()

const auth = useAuthStore()

// ── Badges ────────────────────────────────────────────────────────────────────

const FUNCTION_LABELS: Record<string, string> = {
  // Count
  count:           'CNT',
  count_values:    'CV',
  count_empty:     'CE',
  count_not_empty: 'CNE',
  count_unique:    'UNQ',
  // Percent
  percent_empty:       '%E',
  percent_not_empty:   '%NE',
  percent_checked:     '%OK',
  percent_unchecked:   '%NO',
  percent_per_option:  '%OPT',
  // Checkbox
  checked:             'CHK',
  // Numeric
  sum:    'SUM',
  avg:    'AVG',
  median: 'MED',
  min:    'MIN',
  max:    'MAX',
  range:  'RNG',
  // Raw
  show_original: 'ORG',
  first_value:   '1ST',
  last_value:    'LST',
  // Date
  earliest_date: 'ERL',
  latest_date:   'LAT',
  date_range:    'DRG',
}

// ── Data access ───────────────────────────────────────────────────────────────

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const cellData = computed(() => getCellValue(props.entry, props.schema.id) as any)

const functionKey = computed<string>(() =>
  cellData.value?.function ?? props.schema.config?.function ?? '',
)

const badge = computed(() =>
  FUNCTION_LABELS[functionKey.value] ?? functionKey.value.toUpperCase(),
)

// The function-type badge (ERL / LAT / SUM …) is opt-in per rollup property,
// off by default (#10). Set via the property settings modal.
const showTypeBadge = computed<boolean>(() => props.schema.config?.show_type_badge === true)

// Chip wrapping (show_original / percent_per_option) is opt-in per rollup
// property (#12). Off by default: chips stay on one line and clip; on: wrap.
const wrapContent = computed<boolean>(() => props.schema.config?.wrapContent === true)

const hasError = computed(() => !!cellData.value?.error)
const errorMessage = computed(() => cellData.value?.error ?? '')

// Relation rollups (show_original / first_value / last_value over a relation
// target) carry resolved entry descriptors and a `relation: true` flag so the
// cell can render clickable chips that open the entry (#11).
interface RelationChip { id: string; title: string; database_id: string | null }

const isRelation = computed<boolean>(() => cellData.value?.relation === true)

const relationEntries = computed<RelationChip[]>(() => {
  if (!isRelation.value) return []
  const r = cellData.value?.result
  if (Array.isArray(r)) return r as RelationChip[]
  if (r && typeof r === 'object') return [r as RelationChip]
  return []
})

// ── Result classification ─────────────────────────────────────────────────────

type ResultKind = 'empty' | 'scalar' | 'percent' | 'list' | 'option_map' | 'relation'

const PERCENT_FUNCTIONS = new Set([
  'percent_empty', 'percent_not_empty', 'percent_checked', 'percent_unchecked',
])

const resultKind = computed<ResultKind>(() => {
  if (hasError.value) return 'empty'
  const r = cellData.value?.result
  if (r === null || r === undefined) return 'empty'
  if (isRelation.value) return 'relation'
  if (functionKey.value === 'percent_per_option') return 'option_map'
  if (functionKey.value === 'show_original') return 'list'
  if (Array.isArray(r)) return 'list'
  if (typeof r === 'object') return 'option_map'
  if (PERCENT_FUNCTIONS.has(functionKey.value)) return 'percent'
  return 'scalar'
})

// ── Side view (open related entry from a relation chip) ────────────────────────

const sideViewEntry = ref<RelationChip | null>(null)

function openEntry(chip: RelationChip): void {
  if (chip.database_id) sideViewEntry.value = chip
}

function closeSideView(): void {
  sideViewEntry.value = null
}

// ── Scalar display ────────────────────────────────────────────────────────────

const scalarDisplay = computed<string>(() => {
  const r = cellData.value?.result
  if (r === null || r === undefined) return ''
  if (typeof r === 'number') {
    const formatted = Number.isInteger(r) ? String(r) : r.toFixed(2).replace(/\.?0+$/, '')
    return resultKind.value === 'percent' ? `${formatted} %` : formatted
  }
  if (typeof r === 'boolean') return r ? 'true' : 'false'
  // Date aggregations (earliest_date / latest_date / date_range) arrive as
  // canonical ISO strings; render them in the user's preferred format (#10).
  return maybeFormatRollupDate(String(r), auth.dateFormat)
})

// ── List display (show_original) ──────────────────────────────────────────────

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const listItems = computed<string[]>(() => {
  const r = cellData.value?.result
  if (!Array.isArray(r)) return []
  return r.map((v: unknown) => {
    if (v === null || v === undefined) return '—'
    if (typeof v === 'boolean') return v ? 'true' : 'false'
    if (typeof v === 'number') {
      return Number.isInteger(v) ? String(v) : v.toFixed(2).replace(/\.?0+$/, '')
    }
    // show_original of a date column yields canonical ISO strings; format them.
    return maybeFormatRollupDate(String(v), auth.dateFormat)
  })
})

// ── Option-map display (percent_per_option) ────────────────────────────────────

const optionEntries = computed<{ label: string; pct: string }[]>(() => {
  const r = cellData.value?.result
  if (!r || typeof r !== 'object' || Array.isArray(r)) return []
  return Object.entries(r as Record<string, number>)
    .sort((a, b) => b[1] - a[1])
    .map(([label, pct]) => ({
      label,
      pct: Number.isInteger(pct) ? `${pct} %` : `${pct.toFixed(1)} %`,
    }))
})
</script>

<template>
  <span
    class="rollup-cell"
    :class="{
      'rollup-cell--error': hasError,
      'rollup-cell--list':  resultKind === 'list' || resultKind === 'option_map' || resultKind === 'relation',
      'rollup-cell--wrap':  wrapContent,
    }"
    :title="hasError ? errorMessage : undefined"
  >
    <!-- Function-type badge (opt-in, #10) -->
    <span v-if="badge && showTypeBadge" class="rollup-cell__badge">{{ badge }}</span>

    <!-- Error state -->
    <template v-if="hasError">
      <span class="rollup-cell__error-text">Error</span>
    </template>

    <!-- Empty -->
    <template v-else-if="resultKind === 'empty'" />

    <!-- Scalar / percent -->
    <template v-else-if="resultKind === 'scalar' || resultKind === 'percent'">
      <span class="rollup-cell__value">{{ scalarDisplay }}</span>
    </template>

    <!-- show_original: chip per entry -->
    <template v-else-if="resultKind === 'list'">
      <span class="rollup-cell__chips">
        <span
          v-for="(item, i) in listItems"
          :key="i"
          class="rollup-cell__chip"
          :class="{ 'rollup-cell__chip--null': item === '—' }"
        >{{ item }}</span>
      </span>
    </template>

    <!-- relation: clickable chips that open the related entry -->
    <template v-else-if="resultKind === 'relation'">
      <span class="rollup-cell__chips">
        <span
          v-for="chip in relationEntries"
          :key="chip.id"
          class="rollup-cell__rel-chip"
          :title="chip.title || undefined"
          @click.stop="openEntry(chip)"
        >
          <Icon icon="mdi:file-outline" width="10" height="10" class="rollup-cell__rel-chip-icon" />
          <span class="rollup-cell__rel-chip-text">{{ chip.title || '—' }}</span>
        </span>
      </span>
      <SideView
        v-if="sideViewEntry && sideViewEntry.database_id"
        :database-id="sideViewEntry.database_id"
        :entry-id="sideViewEntry.id"
        @close="closeSideView"
      />
    </template>

    <!-- percent_per_option: option label + percentage -->
    <template v-else-if="resultKind === 'option_map'">
      <span class="rollup-cell__chips">
        <span
          v-for="entry in optionEntries"
          :key="entry.label"
          class="rollup-cell__chip rollup-cell__chip--option"
        >
          <span class="rollup-cell__chip-label">{{ entry.label }}</span>
          <span class="rollup-cell__chip-pct">{{ entry.pct }}</span>
        </span>
      </span>
    </template>
  </span>
</template>

<style scoped>
.rollup-cell {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 12px;
  font-size: 0.875rem;
  min-height: 36px;
  overflow: hidden;
  white-space: nowrap;
  cursor: default;
}

/* List/option-map layout. Default: chips flow and wrap within the column.
 * wrapContent on (rollup-cell--wrap): one chip per line (#12). */
.rollup-cell--list {
  white-space: normal;
  flex-wrap: wrap;
  align-items: flex-start;
}

.rollup-cell--error {
  color: #e05555;
  cursor: help;
}

.rollup-cell__badge {
  flex-shrink: 0;
  font-size: 0.65rem;
  font-weight: 700;
  letter-spacing: 0.05em;
  color: var(--color-text-muted);
  background: var(--color-hover);
  border: 1px solid var(--color-border);
  border-radius: 3px;
  padding: 1px 4px;
  line-height: 1.4;
}

.rollup-cell__value,
.rollup-cell__error-text {
  color: var(--color-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Chip list (show_original + percent_per_option) */
.rollup-cell__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 3px;
  align-items: center;
  min-width: 0;
}

/* wrapContent enabled: one chip per line. */
.rollup-cell--wrap .rollup-cell__chips {
  flex-direction: column;
  flex-wrap: nowrap;
  align-items: flex-start;
}

.rollup-cell__chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 0.78rem;
  padding: 1px 6px;
  border-radius: 3px;
  background: var(--color-hover);
  border: 1px solid var(--color-border);
  color: var(--color-text);
  white-space: nowrap;
}

/* Stack mode (wrapContent on): show full value, wrap long content inside the chip. */
.rollup-cell--wrap .rollup-cell__chip {
  white-space: normal;
  word-break: break-word;
  max-width: 100%;
  align-items: flex-start;
}

.rollup-cell__chip--null {
  color: var(--color-text-muted);
  font-style: italic;
}

.rollup-cell__chip--option {
  background: var(--color-hover);
}

.rollup-cell__chip-label {
  font-weight: 500;
}

.rollup-cell__chip-pct {
  font-size: 0.72rem;
  color: var(--color-text-muted);
}

/* ── Relation chips (clickable, open the related entry) ────────────────────── */
.rollup-cell__rel-chip {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  background: var(--color-accent-subtle);
  border: 1px solid var(--color-accent);
  border-radius: 3px;
  padding: 1px 6px;
  font-size: 0.78rem;
  color: var(--color-text);
  max-width: 130px;
  cursor: pointer;
}

.rollup-cell__rel-chip:hover .rollup-cell__rel-chip-text {
  text-decoration: underline;
}

.rollup-cell__rel-chip-icon {
  flex-shrink: 0;
  color: var(--color-text-muted);
}

.rollup-cell__rel-chip-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Stack mode (wrapContent on): full title with internal wrapping. */
.rollup-cell--wrap .rollup-cell__rel-chip {
  max-width: 100%;
  align-items: flex-start;
}

.rollup-cell--wrap .rollup-cell__rel-chip-text {
  white-space: normal;
  overflow: visible;
  text-overflow: clip;
  word-break: break-word;
}
</style>

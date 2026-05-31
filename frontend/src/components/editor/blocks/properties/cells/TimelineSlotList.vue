<script setup lang="ts">
/**
 * TimelineSlotList
 *
 * Shared display component for timeline-enabled properties in "show all" mode.
 * Renders each _timeline slot as a row:
 *
 *   [period label (gray)]  [value — type-specific rendering]
 *
 * For select / multiselect types the value is rendered as coloured chips so
 * the visual language matches the normal cell display.  All other types use
 * the formatSlotScalar plain-text helper.
 */
import { computed } from 'vue'
import { normalizeSelectOption, optionColorStyle, type DatabaseEntry, type PropertySchema } from '@/stores/database'
import { getRawCellValue, formatSlotScalar, formatPeriodKey } from './cellUtils'

const props = defineProps<{
  entry: DatabaseEntry
  schema: PropertySchema
}>()

const isSelect    = computed(() => props.schema.type === 'select')
const isMulti     = computed(() => isSelect.value && props.schema.config?.mode === 'multiple')
const schemaOpts  = computed(() =>
  ((props.schema.config?.options as unknown[] | undefined) ?? []).map(normalizeSelectOption)
)

function chipStyle(label: string) {
  const opt = schemaOpts.value.find(o => o.label === label)
  return opt ? optionColorStyle(opt.color) : undefined
}

interface SlotRow {
  key: string
  period: string
  valueStr: string      // plain text — used for non-select types
  options: string[]     // chip labels — used for select types
}

const rows = computed<SlotRow[]>(() => {
  const raw = getRawCellValue(props.entry, props.schema.id)
  if (!raw || !('_timeline' in raw)) return []
  const timeline = raw._timeline as Record<string, unknown>
  const keys = Object.keys(timeline)
  if (keys.length === 0) return []

  const sorted = keys.slice().sort((a, b) => {
    const as_ = a === '' ? '' : (a.startsWith('→') ? '' : a.split('→')[0])
    const bs_ = b === '' ? '' : (b.startsWith('→') ? '' : b.split('→')[0])
    return as_ < bs_ ? -1 : as_ > bs_ ? 1 : 0
  })

  return sorted.map(k => {
    const sv = timeline[k] as Record<string, unknown>
    let options: string[] = []
    if (isMulti.value) {
      options = (sv?.options as string[] | undefined) ?? []
    } else if (isSelect.value) {
      const opt = (sv?.option as string | undefined) ?? ''
      if (opt) options = [opt]
    }
    return {
      key: k,
      period: formatPeriodKey(k),
      valueStr: formatSlotScalar(sv ?? {}, props.schema),
      options,
    }
  })
})
</script>

<template>
  <div class="tsl">
    <div v-for="row in rows" :key="row.key" class="tsl__row">
      <span class="tsl__period">{{ row.period }}</span>

      <!-- select / multiselect: render chips with colours -->
      <div v-if="isSelect" class="tsl__chips">
        <span
          v-for="label in row.options"
          :key="label"
          class="tsl__chip"
          :style="chipStyle(label)"
        >{{ label }}</span>
        <span v-if="row.options.length === 0" class="tsl__empty">—</span>
      </div>

      <!-- all other types: plain text -->
      <span v-else class="tsl__value">{{ row.valueStr || '—' }}</span>
    </div>
    <div v-if="rows.length === 0" class="tsl__empty tsl__empty--block">—</div>
  </div>
</template>

<style scoped>
.tsl {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 5px 12px;
  width: 100%;
  min-height: 36px;
  justify-content: center;
}

.tsl__row {
  display: flex;
  align-items: center;
  gap: 7px;
  min-height: 18px;
}

.tsl__period {
  font-size: 0.68rem;
  color: var(--color-text-muted);
  white-space: nowrap;
  flex-shrink: 0;
  min-width: 130px;
}

.tsl__value {
  font-size: 0.82rem;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tsl__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 3px;
  align-items: center;
}

.tsl__chip {
  display: inline-flex;
  align-items: center;
  border-radius: 3px;
  padding: 1px 6px;
  font-size: 0.73rem;
  border: 1px solid;
  white-space: nowrap;
}

.tsl__empty {
  font-size: 0.72rem;
  color: var(--color-text-muted);
  font-style: italic;
}

.tsl__empty--block {
  padding: 0;
}
</style>

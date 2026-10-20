/**
 * useDatabaseGrouping
 *
 * Grouping of a table view by one property, and the per-column aggregations
 * shown in the footer of the table and of every group.
 *
 * Grouping
 * --------
 * A table view with ``groupBySchemaId`` splits its loaded entries by the value
 * of that property. Select groups follow the configured option order, then
 * values no option carries any more; every other type keeps the order in
 * which the values first appear. Entries without a value form the last group.
 * A combination of options of a multi-select forms a group of its own.
 *
 * Aggregations
 * ------------
 * Every column may show one aggregation, stored per view in
 * ``columnAggregations``: a count of non-empty values for every column, and
 * sum, average, minimum and maximum for number and formula columns.
 *
 * Usage:
 *   const grouping = useDatabaseGrouping({ activeView, schemas, entries, saveViews, editable, t })
 */
import { computed, ref, type Ref } from 'vue'
import {
  NAME_COL_KEY,
  optionLabel,
  schemaOptions,
  type AggregationType,
  type DatabaseEntry,
  type DatabaseView,
  type PropertySchema,
} from '@/stores/database'

// ── Grouping ──────────────────────────────────────────────────────────────────

/** One group of a grouped table. */
export interface TableGroup {
  key: string
  label: string
  entries: DatabaseEntry[]
  /** The value a row added to this group starts with; null for no value. */
  groupValue: Record<string, unknown> | null
}

/** Separator of the option ids in the key of a multi-select group. */
const MULTI_KEY_SEPARATOR = '\x00'

function isMultiSelect(schema: PropertySchema): boolean {
  return ((schema.config?.mode as string | undefined) ?? 'single') === 'multiple'
}

/** The group *entry* belongs to when grouped by *schema*; '' for none. */
export function entryGroupKey(entry: DatabaseEntry, schema: PropertySchema): string {
  const val = entry.values[schema.id]
  if (!val) return ''
  switch (schema.type) {
    case 'select': {
      if (isMultiSelect(schema)) {
        const opts = (val.options as string[] | undefined) ?? []
        return [...opts].sort().join(MULTI_KEY_SEPARATOR)
      }
      return (val.option as string | undefined) ?? ''
    }
    case 'text':     return (val.text as string | undefined) ?? ''
    case 'number':   return val.number != null ? String(val.number) : ''
    case 'checkbox': return String(val.checked ?? false)
    case 'email':
    case 'phone':
    case 'url':      return (val.value as string | undefined) ?? ''
    default:         return ''
  }
}

/** The stored value that puts an entry into group *key*; null for none. */
export function groupKeyToValue(key: string, schema: PropertySchema): Record<string, unknown> | null {
  if (!key) return null
  switch (schema.type) {
    case 'select':
      return isMultiSelect(schema) ? { options: key.split(MULTI_KEY_SEPARATOR) } : { option: key }
    case 'text':     return { text: key }
    case 'number':   return { number: parseFloat(key) }
    case 'checkbox': return { checked: key === 'true' }
    case 'email':
    case 'phone':
    case 'url':      return { value: key }
    default:         return null
  }
}

/** What group *key* reads as: option labels for select, else the key itself. */
export function groupLabel(
  key: string,
  schema: PropertySchema,
  t: (key: string) => string,
): string {
  if (key === '') return t('db.groupBy.ungrouped')
  if (schema.type === 'checkbox') {
    return key === 'true' ? t('db.filter.checkboxTrue') : t('db.filter.checkboxFalse')
  }
  if (schema.type === 'select') {
    const options = schemaOptions(schema)
    return key.split(MULTI_KEY_SEPARATOR).map(id => optionLabel(options, id)).filter(Boolean).join(', ')
      || t('db.groupBy.ungrouped')
  }
  return key
}

/** *entries* split into the groups of *schema*, in display order. */
export function buildTableGroups(
  entries: readonly DatabaseEntry[],
  schema: PropertySchema,
  t: (key: string) => string,
): TableGroup[] {
  const groupMap = new Map<string, TableGroup>()
  for (const entry of entries) {
    const key = entryGroupKey(entry, schema)
    if (!groupMap.has(key)) {
      groupMap.set(key, {
        key,
        label: groupLabel(key, schema, t),
        entries: [],
        groupValue: groupKeyToValue(key, schema),
      })
    }
    groupMap.get(key)!.entries.push(entry)
  }

  const valued = [...groupMap.keys()].filter(k => k !== '')
  let orderedKeys: string[]
  if (schema.type === 'select' && !isMultiSelect(schema)) {
    const optionIds = schemaOptions(schema).map(o => o.id)
    orderedKeys = [
      ...optionIds.filter(id => groupMap.has(id)),
      ...valued.filter(k => !optionIds.includes(k)),
    ]
  } else {
    orderedKeys = valued
  }
  if (groupMap.has('')) orderedKeys.push('')
  return orderedKeys.map(k => groupMap.get(k)!)
}

// ── Aggregations ──────────────────────────────────────────────────────────────

const AGG_BADGES: Record<AggregationType, string> = {
  none: '', count: 'CNT', sum: 'SUM', avg: 'AVG', min: 'MIN', max: 'MAX',
}

/** The short label of aggregation *fn* in the footer and the picker. */
export function aggregationBadge(fn: AggregationType): string {
  return AGG_BADGES[fn] ?? ''
}

/** The aggregations a column offers: numeric ones for number and formula columns. */
export function aggregationOptions(schema: PropertySchema | null): AggregationType[] {
  const isNum = schema?.type === 'number' || schema?.type === 'formula'
  const base: AggregationType[] = ['none', 'count']
  if (isNum) base.push('sum', 'avg', 'min', 'max')
  return base
}

function formatAggregateNumber(n: number, schema: PropertySchema | null): string {
  const raw = Number.isInteger(n) ? String(n) : n.toFixed(2).replace(/\.?0+$/, '')
  if (schema?.type === 'number' && (schema.config?.format as string | undefined) === 'euro') {
    return `${raw} €`
  }
  return raw
}

/**
 * The aggregation *fn* of column *colKey* over *entries*, formatted; '' when
 * there is nothing to show.
 */
export function computeAggregation(
  entries: readonly DatabaseEntry[],
  colKey: string,
  schema: PropertySchema | null,
  fn: AggregationType,
): string {
  if (fn === 'none') return ''
  if (fn === 'count') {
    const nonEmpty = entries.filter(e => {
      if (colKey === NAME_COL_KEY) return !!((e.content?.title as string | undefined) ?? '').trim()
      return e.values[schema!.id] != null
    })
    return String(nonEmpty.length)
  }
  const numbers: number[] = []
  for (const entry of entries) {
    let n: number | null = null
    if (schema?.type === 'number') {
      const raw = entry.values[schema.id]?.number
      if (typeof raw === 'number' && !isNaN(raw)) n = raw
    } else if (schema?.type === 'formula') {
      const result = entry.values[schema.id]?.result
      if (typeof result === 'number' && !isNaN(result)) n = result
    }
    if (n !== null) numbers.push(n)
  }
  if (numbers.length === 0) return ''
  switch (fn) {
    case 'sum': return formatAggregateNumber(numbers.reduce((a, b) => a + b, 0), schema)
    case 'avg': return formatAggregateNumber(numbers.reduce((a, b) => a + b, 0) / numbers.length, schema)
    case 'min': return formatAggregateNumber(Math.min(...numbers), schema)
    case 'max': return formatAggregateNumber(Math.max(...numbers), schema)
    default:    return ''
  }
}

// ── Composable ────────────────────────────────────────────────────────────────

export function useDatabaseGrouping(options: {
  activeView: Readonly<Ref<DatabaseView | null | undefined>>
  schemas: Readonly<Ref<PropertySchema[]>>
  /** The loaded entries, in the order the server sorted them. */
  entries: Readonly<Ref<DatabaseEntry[]>>
  saveViews: () => Promise<void>
  editable: Readonly<Ref<boolean>>
  t: (key: string) => string
}) {
  const { activeView, schemas, entries, saveViews, editable, t } = options

  /** The property the active view groups by, or null. */
  const groupBySchema = computed<PropertySchema | null>(() => {
    const id = activeView.value?.groupBySchemaId
    if (!id) return null
    return schemas.value.find(s => s.id === id) ?? null
  })

  /** Whether the table is grouped; only table views group. */
  const isGrouped = computed<boolean>(() =>
    (activeView.value?.viewType === 'table' || !activeView.value) &&
    groupBySchema.value !== null,
  )

  const tableGroups = computed<TableGroup[]>(() =>
    groupBySchema.value ? buildTableGroups(entries.value, groupBySchema.value, t) : [],
  )

  /** Key of the open aggregation picker: ``flat:<col>`` or ``<group>:<col>``. */
  const aggPickerKey = ref<string | null>(null)

  /** The aggregation the active view shows for column *colKey*. */
  function getAggregationFn(colKey: string): AggregationType {
    return (activeView.value?.columnAggregations?.[colKey] as AggregationType | undefined) ?? 'none'
  }

  function setAggregation(colKey: string, fn: AggregationType): void {
    const view = activeView.value
    if (!view) return
    view.columnAggregations = { ...view.columnAggregations, [colKey]: fn }
    saveViews()
    aggPickerKey.value = null
  }

  /** Open or close the aggregation picker *key*; offered with write access only. */
  function toggleAggPicker(key: string): void {
    if (!editable.value) return
    aggPickerKey.value = aggPickerKey.value === key ? null : key
  }

  return {
    groupBySchema,
    isGrouped,
    tableGroups,
    aggPickerKey,
    getAggregationFn,
    setAggregation,
    toggleAggPicker,
  }
}

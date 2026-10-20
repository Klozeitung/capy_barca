import { describe, it, expect, vi } from 'vitest'
import { ref } from 'vue'
import {
  aggregationBadge,
  aggregationOptions,
  buildTableGroups,
  computeAggregation,
  entryGroupKey,
  groupKeyToValue,
  useDatabaseGrouping,
} from '@/composables/useDatabaseGrouping'
import { NAME_COL_KEY, type DatabaseEntry, type DatabaseView, type PropertySchema } from '@/stores/database'

const t = (key: string) => key

function schema(id: string, type: string, config: Record<string, unknown> = {}): PropertySchema {
  return { id, database_id: 'db-a', name: id, type, config, position: 0, group: 'Standard' }
}

function entry(id: string, values: DatabaseEntry['values'] = {}, title = id): DatabaseEntry {
  return { id, position: 0, content: { title }, icon: null, state: 'active', values }
}

const status = schema('status', 'select', {
  options: [{ id: 'opt-a', label: 'Value A' }, { id: 'opt-b', label: 'Value B' }],
})
const tags = schema('tags', 'select', {
  mode: 'multiple',
  options: [{ id: 'opt-a', label: 'Value A' }, { id: 'opt-b', label: 'Value B' }],
})

// ── Group keys ────────────────────────────────────────────────────────────────

describe('entryGroupKey / groupKeyToValue', () => {
  it('round-trips the supported types', () => {
    const cases: [PropertySchema, Record<string, unknown>][] = [
      [status, { option: 'opt-a' }],
      [tags, { options: ['opt-a', 'opt-b'] }],
      [schema('t', 'text'), { text: 'Text A' }],
      [schema('n', 'number'), { number: 7 }],
      [schema('c', 'checkbox'), { checked: true }],
      [schema('u', 'url'), { value: 'https://example.com' }],
    ]
    for (const [s, value] of cases) {
      const key = entryGroupKey(entry('x', { [s.id]: value }), s)
      expect(groupKeyToValue(key, s)).toEqual(value)
    }
  })

  it('groups a multi-select by its combination of options, in any order', () => {
    const one = entryGroupKey(entry('x', { tags: { options: ['opt-b', 'opt-a'] } }), tags)
    const two = entryGroupKey(entry('y', { tags: { options: ['opt-a', 'opt-b'] } }), tags)
    expect(one).toBe(two)
  })

  it('puts entries without a value into the empty group', () => {
    expect(entryGroupKey(entry('x'), status)).toBe('')
    expect(groupKeyToValue('', status)).toBeNull()
  })
})

// ── Groups ────────────────────────────────────────────────────────────────────

describe('buildTableGroups', () => {
  it('orders select groups by option, then unknown ids, then the empty group', () => {
    const groups = buildTableGroups([
      entry('1'),
      entry('2', { status: { option: 'opt-b' } }),
      entry('3', { status: { option: 'gone' } }),
      entry('4', { status: { option: 'opt-a' } }),
      entry('5', { status: { option: 'opt-b' } }),
    ], status, t)
    expect(groups.map(g => g.key)).toEqual(['opt-a', 'opt-b', 'gone', ''])
    expect(groups.map(g => g.label)).toEqual(['Value A', 'Value B', 'db.groupBy.ungrouped', 'db.groupBy.ungrouped'])
    expect(groups[1].entries.map(e => e.id)).toEqual(['2', '5'])
    expect(groups[1].groupValue).toEqual({ option: 'opt-b' })
  })

  it('keeps first-seen order for other types and labels checkboxes', () => {
    const check = schema('c', 'checkbox')
    const groups = buildTableGroups([
      entry('1', { c: { checked: false } }),
      entry('2', { c: { checked: true } }),
    ], check, t)
    expect(groups.map(g => g.label)).toEqual(['db.filter.checkboxFalse', 'db.filter.checkboxTrue'])
  })
})

// ── Aggregations ──────────────────────────────────────────────────────────────

describe('aggregations', () => {
  const amount = schema('amount', 'number', { format: 'euro' })
  const entries = [
    entry('1', { amount: { number: 2 } }),
    entry('2', { amount: { number: 3.5 } }),
    entry('3', {}, ''),
  ]

  it('offers numeric aggregations for number and formula columns only', () => {
    expect(aggregationOptions(amount)).toEqual(['none', 'count', 'sum', 'avg', 'min', 'max'])
    expect(aggregationOptions(schema('t', 'text'))).toEqual(['none', 'count'])
    expect(aggregationOptions(null)).toEqual(['none', 'count'])
  })

  it('computes and formats the numeric aggregations', () => {
    expect(computeAggregation(entries, 'amount', amount, 'sum')).toBe('5.5 €')
    expect(computeAggregation(entries, 'amount', amount, 'avg')).toBe('2.75 €')
    expect(computeAggregation(entries, 'amount', amount, 'min')).toBe('2 €')
    expect(computeAggregation(entries, 'amount', amount, 'none')).toBe('')
  })

  it('counts non-empty values and non-empty names', () => {
    expect(computeAggregation(entries, 'amount', amount, 'count')).toBe('2')
    expect(computeAggregation(entries, NAME_COL_KEY, null, 'count')).toBe('2')
  })

  it('reads the numeric result of a formula', () => {
    const formula = schema('f', 'formula')
    const rows = [entry('1', { f: { result: 4 } }), entry('2', { f: { result: 'text' } })]
    expect(computeAggregation(rows, 'f', formula, 'max')).toBe('4')
  })

  it('names each aggregation with its badge', () => {
    expect(aggregationBadge('count')).toBe('CNT')
    expect(aggregationBadge('none')).toBe('')
  })
})

// ── Composable ────────────────────────────────────────────────────────────────

describe('useDatabaseGrouping', () => {
  function view(overrides: Partial<DatabaseView> = {}): DatabaseView {
    return {
      id: 'view-a', name: 'View A', viewType: 'table', colOrder: [], colWidths: {},
      filterGroups: [], sorts: [], hiddenColumns: [],
      ...overrides,
    } as DatabaseView
  }

  it('groups table views only', () => {
    const activeView = ref<DatabaseView | null>(view({ groupBySchemaId: 'status' }))
    const grouping = useDatabaseGrouping({
      activeView,
      schemas: ref([status]),
      entries: ref([entry('1', { status: { option: 'opt-a' } })]),
      saveViews: async () => {},
      editable: ref(true),
      t,
    })
    expect(grouping.isGrouped.value).toBe(true)
    expect(grouping.tableGroups.value.map(g => g.key)).toEqual(['opt-a'])
    activeView.value = view({ groupBySchemaId: 'status', viewType: 'calendar' })
    expect(grouping.isGrouped.value).toBe(false)
  })

  it('stores an aggregation in the view and opens the picker only with write access', () => {
    const activeView = ref<DatabaseView | null>(view())
    const saveViews = vi.fn(async () => {})
    const editable = ref(false)
    const grouping = useDatabaseGrouping({
      activeView, schemas: ref([]), entries: ref([]), saveViews, editable, t,
    })

    grouping.toggleAggPicker('flat:amount')
    expect(grouping.aggPickerKey.value).toBeNull()
    editable.value = true
    grouping.toggleAggPicker('flat:amount')
    expect(grouping.aggPickerKey.value).toBe('flat:amount')

    grouping.setAggregation('amount', 'sum')
    expect(grouping.getAggregationFn('amount')).toBe('sum')
    expect(saveViews).toHaveBeenCalledOnce()
    expect(grouping.aggPickerKey.value).toBeNull()
  })
})

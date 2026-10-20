import { describe, it, expect, vi } from 'vitest'
import { ref } from 'vue'
import { initialValuesFromFilters, useAddRow } from '@/composables/useAddRow'
import {
  NAME_COL_KEY,
  type DatabaseView,
  type FilterOperator,
  type PropertySchema,
  type ViewFilter,
} from '@/stores/database'

function schema(id: string, type: string, config: Record<string, unknown> = {}): PropertySchema {
  return { id, database_id: 'db-a', name: id, type, config, position: 0, group: 'Standard' }
}

function filter(schemaId: string, operator: FilterOperator, value = '', extra: Partial<ViewFilter> = {}): ViewFilter {
  return { id: `${schemaId}-${operator}`, schemaId, operator, value, ...extra }
}

function view(groups: { conjunction: 'and' | 'or'; filters: ViewFilter[] }[], extra: Partial<DatabaseView> = {}): DatabaseView {
  return {
    id: 'view-a', name: 'View A', viewType: 'table', colOrder: [], colWidths: {},
    filterGroups: groups.map((g, i) => ({ id: `g${i}`, ...g })), sorts: [], hiddenColumns: [],
    ...extra,
  } as DatabaseView
}

const schemas = [
  schema('text', 'text'),
  schema('num', 'number'),
  schema('check', 'checkbox'),
  schema('status', 'select'),
  schema('tags', 'select', { mode: 'multiple' }),
  schema('link', 'relation'),
  schema('when', 'date'),
  schema('calc', 'formula'),
]

describe('initialValuesFromFilters', () => {
  it('derives a satisfying value per supported type', () => {
    const result = initialValuesFromFilters(view([{ conjunction: 'and', filters: [
      filter('text', 'contains', 'Text A'),
      filter('num', 'gt', '4'),
      filter('check', 'eq', 'true'),
      filter('status', 'eq', 'opt-a'),
      filter('tags', 'contains', 'opt-b'),
      filter('link', 'contains', 'entry-a'),
      filter('when', 'eq', '2000-07-28'),
      filter('calc', 'eq', '1'),
    ] }]), schemas)
    expect(result.values).toEqual({
      text: { text: 'Text A' },
      num: { number: 5 },
      check: { checked: true },
      status: { option: 'opt-a' },
      tags: { options: ['opt-b'] },
      link: { related_ids: ['entry-a'] },
      when: { start: '2000-07-28' },
    })
    expect(result.name).toBeNull()
  })

  it('shifts strict date comparisons by a day', () => {
    const result = initialValuesFromFilters(view([{ conjunction: 'and', filters: [
      filter('when', 'lt', '2045-07-17'),
    ] }]), schemas)
    expect(result.values.when).toEqual({ start: '2045-07-16' })
  })

  it('shifts by local calendar days in both directions', () => {
    const result = initialValuesFromFilters(view([{ conjunction: 'and', filters: [
      filter('when', 'gt', '2000-02-28'),
    ] }]), schemas)
    expect(result.values.when).toEqual({ start: '2000-02-29' })
  })

  it('takes today from the local calendar', () => {
    vi.useFakeTimers()
    try {
      // Just after midnight, local time, when the UTC date is still yesterday
      // east of UTC.
      vi.setSystemTime(new Date(2000, 6, 28, 0, 30))
      const result = initialValuesFromFilters(view([{ conjunction: 'and', filters: [
        filter('when', 'eq', '', { dateMode: 'today' }),
        filter('text', 'eq', 'Text A'),
      ] }]), schemas)
      expect(result.values.when).toEqual({ start: '2000-07-28' })
    } finally {
      vi.useRealTimers()
    }
  })

  it('takes a name from a name filter', () => {
    const result = initialValuesFromFilters(view([{ conjunction: 'and', filters: [
      filter(NAME_COL_KEY, 'starts_with', 'Person A'),
    ] }]), schemas)
    expect(result.name).toBe('Person A')
  })

  it('uses only the first satisfiable condition of an OR group', () => {
    const result = initialValuesFromFilters(view([{ conjunction: 'or', filters: [
      filter('calc', 'eq', '1'),
      filter('text', 'eq', 'Text A'),
      filter('num', 'eq', '3'),
    ] }]), schemas)
    expect(result.values).toEqual({ text: { text: 'Text A' } })
  })

  it('derives nothing without a view or filters', () => {
    expect(initialValuesFromFilters(null, schemas)).toEqual({ values: {}, name: null })
    expect(initialValuesFromFilters(view([]), schemas)).toEqual({ values: {}, name: null })
  })
})

describe('useAddRow', () => {
  function setup(activeView: DatabaseView, editable = true) {
    const createEntry = vi.fn(async () => ({ id: 'new' }))
    const writeValue = vi.fn(async () => undefined)
    const requery = vi.fn(async () => {})
    const onCreated = vi.fn(async () => {})
    const adding = useAddRow({
      databaseId: 'db-a',
      activeView: ref(activeView),
      schemas: ref(schemas),
      editable: ref(editable),
      createEntry, writeValue, requery, onCreated,
    })
    return { adding, createEntry, writeValue, requery, onCreated }
  }

  it('writes the derived values, reloads and focuses the new row', async () => {
    const { adding, writeValue, requery, onCreated } = setup(view([{ conjunction: 'and', filters: [
      filter('text', 'eq', 'Text A'), filter(NAME_COL_KEY, 'eq', 'Person A'),
    ] }]))
    await adding.addRow()
    expect(writeValue).toHaveBeenCalledWith('new', 'text', { text: 'Text A' })
    expect(requery).toHaveBeenCalledOnce()
    expect(onCreated).toHaveBeenCalledWith('new', 'Person A')
    expect(adding.isAddingRow.value).toBe(false)
  })

  it('gives a group row the value of its group', async () => {
    const { adding, writeValue } = setup(view([], { groupBySchemaId: 'status' }))
    await adding.addGroupRow({ option: 'opt-b' })
    expect(writeValue).toHaveBeenCalledWith('new', 'status', { option: 'opt-b' })
  })

  it('puts a calendar entry on its day without focusing it', async () => {
    const { adding, writeValue, onCreated } = setup(view([], { calendarDateSchemaId: 'when' }))
    await adding.addRowOnDate('2000-07-28')
    expect(writeValue).toHaveBeenCalledWith('new', 'when', { start: '2000-07-28', end: '2000-07-28' })
    expect(onCreated).not.toHaveBeenCalled()
  })

  it('creates nothing without write access', async () => {
    const { adding, createEntry } = setup(view([]), false)
    await adding.addRow()
    expect(createEntry).not.toHaveBeenCalled()
  })
})

/**
 * useAddRow
 *
 * Creating entries from a database view, so that a new entry stays visible.
 *
 * A new entry starts with values derived from the view's filters: a filter
 * "Status is Value A" gives it Value A, a filter on the name gives it a name.
 * In an AND group every condition contributes; in an OR group the first one
 * that can be satisfied does. Rows added to a group of a grouped table start
 * with the group's value, rows added on a calendar day with that date.
 *
 * Dates are derived in local time: "today" is the user's calendar day, and a
 * strict comparison ("after X") moves one calendar day away from X.
 *
 * Usage:
 *   const adding = useAddRow({ databaseId, activeView, schemas, editable, createEntry, writeValue, requery, onCreated })
 *   // template: @click="adding.addRow"
 */
import { ref, type Ref } from 'vue'
import { formatDateToIso, parseIsoToDate } from '@/composables/dateValue'
import {
  NAME_COL_KEY,
  type DatabaseView,
  type FilterOperator,
  type PropertySchema,
  type ViewFilter,
} from '@/stores/database'

/** Values a new entry starts with, by schema id, and its name. */
export interface InitialValues {
  values: Record<string, Record<string, unknown>>
  name: string | null
}

// Property types no filter value is derived for: system-managed and computed
// properties, and files.
const SKIP_TYPES = new Set([
  'id', 'created_by', 'created_time', 'last_edited_by', 'last_edited_time',
  'formula', 'rollup', 'file',
])

/** The user's calendar day, as ``YYYY-MM-DD``. */
function todayISO(): string {
  return formatDateToIso(new Date(), false)
}

/**
 * *iso* moved by *days* calendar days, in local time. Reading the result back
 * in UTC would move it by one more day east of UTC. A value that is no date is
 * returned unchanged.
 */
function shiftDate(iso: string, days: number): string {
  const d = parseIsoToDate(iso)
  if (!d) return iso
  d.setDate(d.getDate() + days)
  return formatDateToIso(d, false)
}

function resolveDateRef(filter: ViewFilter): string | null {
  const mode = filter.dateMode ?? 'exact'
  const today = todayISO()
  if (mode === 'today') return today
  if (mode === 'relative') return shiftDate(today, filter.dateOffset ?? 0)
  return filter.value || null
}

/** A value of *schema* that satisfies *filter*, or null when none can be derived. */
function valueForFilter(filter: ViewFilter, schema: PropertySchema): Record<string, unknown> | null {
  const { operator, value } = filter
  switch (schema.type) {
    case 'text':
    case 'email':
    case 'phone':
    case 'url': {
      if (!value || !['eq', 'contains', 'starts_with'].includes(operator)) return null
      const key = schema.type === 'text' ? 'text' : 'value'
      return { [key]: value }
    }
    case 'number': {
      const num = parseFloat(value)
      if (isNaN(num)) return null
      if (operator === 'eq' || operator === 'gte' || operator === 'lte') return { number: num }
      if (operator === 'gt') return { number: num + 1 }
      if (operator === 'lt') return { number: num - 1 }
      return null
    }
    case 'checkbox':
      if (operator === 'eq') return { checked: value === 'true' }
      return null
    case 'select': {
      const isMulti = (schema.config?.mode as string | undefined) === 'multiple'
      if (isMulti) {
        if (operator === 'contains' && value) return { options: [value] }
        return null
      }
      if (operator === 'eq' && value) return { option: value }
      return null
    }
    case 'relation':
      if (operator === 'contains' && value) return { related_ids: [value] }
      return null
    case 'date': {
      const today = todayISO()
      const presetMap: Partial<Record<FilterOperator, string>> = {
        past_week:  shiftDate(today, -1),
        past_month: shiftDate(today, -1),
        past_year:  shiftDate(today, -1),
        this_week:  today,
        next_week:  shiftDate(today, 1),
        next_month: shiftDate(today, 1),
        next_year:  shiftDate(today, 1),
      }
      if (operator in presetMap) return { start: presetMap[operator as keyof typeof presetMap]! }
      if (operator === 'between') {
        const ref = filter.value || null
        if (!ref) return null
        return { start: ref }
      }
      const ref = resolveDateRef(filter)
      if (!ref) return null
      if (operator === 'eq' || operator === 'gte' || operator === 'lte') return { start: ref }
      if (operator === 'gt') return { start: shiftDate(ref, 1) }
      if (operator === 'lt') return { start: shiftDate(ref, -1) }
      return null
    }
    default:
      return null
  }
}

function nameForFilter(filter: ViewFilter): string | null {
  if (!['eq', 'contains', 'starts_with'].includes(filter.operator)) return null
  return filter.value || null
}

/** The values and the name a new entry of *view* starts with. */
export function initialValuesFromFilters(
  view: DatabaseView | null | undefined,
  schemas: readonly PropertySchema[],
): InitialValues {
  if (!view || view.filterGroups.length === 0) return { values: {}, name: null }

  function derive(filter: ViewFilter): Record<string, unknown> | null {
    const schema = schemas.find(s => s.id === filter.schemaId)
    if (!schema || SKIP_TYPES.has(schema.type)) return null
    return valueForFilter(filter, schema)
  }

  const values: Record<string, Record<string, unknown>> = {}
  let name: string | null = null

  for (const group of view.filterGroups) {
    if (group.conjunction === 'and') {
      for (const filter of group.filters) {
        if (filter.schemaId === NAME_COL_KEY) {
          if (name === null) name = nameForFilter(filter)
          continue
        }
        if (values[filter.schemaId] !== undefined) continue
        const v = derive(filter)
        if (v !== null) values[filter.schemaId] = v
      }
    } else {
      let picked = false
      for (const filter of group.filters) {
        if (picked) break
        if (filter.schemaId === NAME_COL_KEY) {
          if (name === null) {
            const n = nameForFilter(filter)
            if (n !== null) { name = n; picked = true }
          }
          continue
        }
        if (values[filter.schemaId] !== undefined) { picked = true; continue }
        const v = derive(filter)
        if (v !== null) { values[filter.schemaId] = v; picked = true }
      }
    }
  }

  return { values, name }
}

export function useAddRow(options: {
  databaseId: string
  activeView: Readonly<Ref<DatabaseView | null | undefined>>
  schemas: Readonly<Ref<PropertySchema[]>>
  editable: Readonly<Ref<boolean>>
  /** Create an entry and return its id. */
  createEntry: () => Promise<{ id: string }>
  writeValue: (entryId: string, schemaId: string, value: Record<string, unknown>) => Promise<unknown>
  /** Load the rows again once the entry has its values. */
  requery: () => Promise<void>
  /** Put the cursor into the new row (table rows only). */
  onCreated: (entryId: string, derivedName: string | null) => Promise<void>
}) {
  const { activeView, schemas, editable, createEntry, writeValue, requery, onCreated } = options

  const isAddingRow = ref(false)

  /**
   * Create an entry with *extra* values on top of those of the filters, load
   * the rows again and, for table rows, focus it. One creation at a time.
   */
  async function create(
    extra: Record<string, Record<string, unknown>>,
    focus: boolean,
  ): Promise<void> {
    if (isAddingRow.value || !editable.value) return
    isAddingRow.value = true
    try {
      const { values, name } = initialValuesFromFilters(activeView.value, schemas.value)
      const created = await createEntry()
      const merged = { ...values, ...extra }
      if (Object.keys(merged).length > 0) {
        await Promise.all(
          Object.entries(merged).map(([schemaId, value]) => writeValue(created.id, schemaId, value)),
        )
      }
      await requery()
      if (focus) await onCreated(created.id, name)
    } finally {
      isAddingRow.value = false
    }
  }

  /** Add a row to the table. */
  async function addRow(): Promise<void> {
    await create({}, true)
  }

  /** Add a row to the group whose rows hold *groupValue*. */
  async function addGroupRow(groupValue: Record<string, unknown> | null): Promise<void> {
    const groupSchemaId = activeView.value?.groupBySchemaId
    await create(groupValue && groupSchemaId ? { [groupSchemaId]: groupValue } : {}, true)
  }

  /** Add an entry on calendar day *dateStr*, in the view's date property. */
  async function addRowOnDate(dateStr: string): Promise<void> {
    const dateSchemaId = activeView.value?.calendarDateSchemaId
    if (!dateSchemaId) return
    await create({ [dateSchemaId]: { start: dateStr, end: dateStr } }, false)
  }

  return { isAddingRow, addRow, addGroupRow, addRowOnDate }
}

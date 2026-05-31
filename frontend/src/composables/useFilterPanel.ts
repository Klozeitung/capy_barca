/**
 * useFilterPanel
 *
 * Two exports:
 *
 * 1. Pure helper functions (exported standalone) – used both by the composable
 *    and by FilterPanel.vue so the component can call them directly without
 *    going through the parent.
 *
 * 2. useFilterPanel composable – owns mutable filter UI state (showFilterPanel,
 *    filterCount) and all filter/group mutation functions.  Callers must supply
 *    the active view, schemas, displayed entries, saveViews and onQueryNeeded.
 */
import { ref, computed, type ComputedRef, type Ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  useDatabaseStore,
  normalizeSelectOption,
  type PropertySchema,
  type DatabaseEntry,
  type DatabaseView,
  type FilterGroup,
  type ViewFilter,
  type FilterOperator,
  type DateFilterMode,
} from '@/stores/database'

// ── Pure helpers (export so FilterPanel.vue can import them directly) ─────────

export const DATE_PRESET_OPERATORS = new Set<FilterOperator>([
  'past_week', 'past_month', 'past_year',
  'this_week', 'next_week', 'next_month', 'next_year',
])

export function filterNeedsValue(operator: FilterOperator): boolean {
  if (operator === 'is_empty' || operator === 'is_not_empty') return false
  if (DATE_PRESET_OPERATORS.has(operator)) return false
  return true
}

/** True only for 'between': signals that a second date input is needed. */
export function filterNeedsValue2(operator: FilterOperator): boolean {
  return operator === 'between'
}

export function getFormulaResultType(
  schemaId: string,
  displayedEntries: DatabaseEntry[],
): string {
  const val = displayedEntries
    .map((e) => e.values[schemaId])
    .find((v) => v != null && (v as Record<string, unknown>).result != null)
  return ((val as Record<string, unknown> | undefined)?.result_type as string | undefined) ?? 'text'
}

export function getOperatorsForSchemaId(
  schemaId: string,
  schemas: PropertySchema[],
  displayedEntries: DatabaseEntry[],
  nameColKey: string,
): FilterOperator[] {
  if (schemaId === nameColKey) {
    return ['contains', 'not_contains', 'starts_with', 'ends_with', 'eq', 'neq', 'is_empty', 'is_not_empty']
  }
  const schema = schemas.find((s) => s.id === schemaId)
  if (!schema) return ['eq']
  switch (schema.type) {
    case 'number':
      return ['eq', 'neq', 'gt', 'gte', 'lt', 'lte', 'is_empty', 'is_not_empty']
    case 'checkbox':
      return ['eq', 'neq', 'is_empty', 'is_not_empty']
    case 'select':
      if ((schema.config?.mode as string | undefined) === 'multiple') {
        return ['contains', 'not_contains', 'is_empty', 'is_not_empty']
      }
      return ['eq', 'neq', 'is_empty', 'is_not_empty']
    case 'date':
    case 'created_time':
    case 'last_edited_time':
      return [
        'eq', 'gt', 'gte', 'lt', 'lte',
        'between',
        'is_empty', 'is_not_empty',
        'past_week', 'past_month', 'past_year',
        'this_week', 'next_week', 'next_month', 'next_year',
      ]
    case 'relation':
      return ['contains', 'not_contains', 'is_empty', 'is_not_empty']
    case 'file':
      return ['is_empty', 'is_not_empty']
    case 'id':
      return ['eq', 'neq', 'gt', 'gte', 'lt', 'lte', 'is_empty', 'is_not_empty']
    case 'created_by':
    case 'last_edited_by':
      return ['contains', 'not_contains', 'is_empty', 'is_not_empty']
    case 'formula': {
      const rt = getFormulaResultType(schema.id, displayedEntries)
      switch (rt) {
        case 'number':
          return ['eq', 'neq', 'gt', 'gte', 'lt', 'lte', 'is_empty', 'is_not_empty']
        case 'boolean':
          return ['eq', 'neq', 'is_empty', 'is_not_empty']
        case 'date':
          return [
            'eq', 'gt', 'gte', 'lt', 'lte',
            'between',
            'is_empty', 'is_not_empty',
            'past_week', 'past_month', 'past_year',
            'this_week', 'next_week', 'next_month', 'next_year',
          ]
        default:
          return ['contains', 'not_contains', 'starts_with', 'ends_with', 'eq', 'neq', 'is_empty', 'is_not_empty']
      }
    }
    default:
      return ['contains', 'not_contains', 'starts_with', 'ends_with', 'eq', 'neq', 'is_empty', 'is_not_empty']
  }
}

export function getFilterSchema(
  filter: ViewFilter,
  schemas: PropertySchema[],
  nameColKey: string,
): PropertySchema | null {
  if (filter.schemaId === nameColKey) return null
  return schemas.find((s) => s.id === filter.schemaId) ?? null
}

export function isDateFilter(
  filter: ViewFilter,
  schemas: PropertySchema[],
  displayedEntries: DatabaseEntry[],
  nameColKey: string,
): boolean {
  const schema = getFilterSchema(filter, schemas, nameColKey)
  if (schema === null) return false
  if (['date', 'created_time', 'last_edited_time'].includes(schema.type)) return true
  if (schema.type === 'formula' && getFormulaResultType(schema.id, displayedEntries) === 'date') return true
  return false
}

export function isSelectFilter(
  filter: ViewFilter,
  schemas: PropertySchema[],
  nameColKey: string,
): boolean {
  const schema = getFilterSchema(filter, schemas, nameColKey)
  return (
    schema !== null &&
    schema.type === 'select' &&
    (schema.config?.mode as string | undefined) !== 'multiple'
  )
}

export function isMultiSelectFilter(
  filter: ViewFilter,
  schemas: PropertySchema[],
  nameColKey: string,
): boolean {
  const schema = getFilterSchema(filter, schemas, nameColKey)
  return (
    schema !== null &&
    schema.type === 'select' &&
    (schema.config?.mode as string | undefined) === 'multiple'
  )
}

export function isCheckboxFilter(
  filter: ViewFilter,
  schemas: PropertySchema[],
  nameColKey: string,
): boolean {
  const schema = getFilterSchema(filter, schemas, nameColKey)
  return schema !== null && schema.type === 'checkbox'
}

export function isRelationFilter(
  filter: ViewFilter,
  schemas: PropertySchema[],
  nameColKey: string,
): boolean {
  const schema = getFilterSchema(filter, schemas, nameColKey)
  return schema !== null && schema.type === 'relation'
}

export function getSelectOptions(
  filter: ViewFilter,
  schemas: PropertySchema[],
  nameColKey: string,
): string[] {
  const schema = getFilterSchema(filter, schemas, nameColKey)
  const raw = (schema?.config?.options as (string | object)[] | undefined) ?? []
  return raw.map((o) => normalizeSelectOption(o).label)
}

// ── Composable ────────────────────────────────────────────────────────────────

export function useFilterPanel(options: {
  activeView: ComputedRef<DatabaseView | null>
  schemas: ComputedRef<PropertySchema[]>
  displayedEntries: Ref<DatabaseEntry[]>
  nameColKey: string
  saveViews: () => Promise<void>
  onQueryNeeded: () => Promise<void>
}) {
  const { activeView, schemas, displayedEntries, nameColKey, saveViews, onQueryNeeded } = options
  const { t } = useI18n()
  const dbStore = useDatabaseStore()

  // ── UI state ────────────────────────────────────────────────────────────────

  const showFilterPanel  = ref(false)
  const filterCount      = computed(() => activeView.value?.filterGroups.length ?? 0)

  // Relation entries cache: populated lazily on first access per schema.
  const relationFilterEntries = ref<Record<string, DatabaseEntry[]>>({})

  function getRelationEntries(schemaId: string): DatabaseEntry[] {
    if (schemaId in relationFilterEntries.value) return relationFilterEntries.value[schemaId]
    const schema   = schemas.value.find((s) => s.id === schemaId)
    const targetId = schema?.config?.target_database_id as string | undefined
    if (!targetId) return []
    relationFilterEntries.value[schemaId] = []
    dbStore.fetchEntries(targetId).then((entries) => {
      relationFilterEntries.value[schemaId] = entries
    })
    return []
  }

  function relationEntryTitle(entry: DatabaseEntry): string {
    return ((entry.content?.title as string | undefined) ?? '').trim() || t('main.untitled')
  }

  // ── Internal helpers ────────────────────────────────────────────────────────

  function _defaultFilterValue(schemaId: string): string {
    if (schemaId === nameColKey) return ''
    const schema = schemas.value.find((s) => s.id === schemaId)
    if (schema?.type === 'checkbox') return 'true'
    return ''
  }

  function _findFilter(groupId: string, filterId: string): ViewFilter | undefined {
    const view = activeView.value
    if (!view) return undefined
    return view.filterGroups.find((g) => g.id === groupId)?.filters.find((f) => f.id === filterId)
  }

  // ── Group management ────────────────────────────────────────────────────────

  async function addGroup(): Promise<void> {
    const view = activeView.value
    if (!view) return
    const firstSchemaId = schemas.value[0]?.id ?? nameColKey
    const newGroup: FilterGroup = {
      id: crypto.randomUUID(),
      conjunction: 'and',
      filters: [{
        id: crypto.randomUUID(),
        schemaId: firstSchemaId,
        operator: getOperatorsForSchemaId(firstSchemaId, schemas.value, displayedEntries.value, nameColKey)[0],
        value: _defaultFilterValue(firstSchemaId),
      }],
    }
    view.filterGroups.push(newGroup)
    await saveViews()
    await onQueryNeeded()
  }

  async function removeGroup(groupId: string): Promise<void> {
    const view = activeView.value
    if (!view) return
    view.filterGroups = view.filterGroups.filter((g) => g.id !== groupId)
    await saveViews()
    await onQueryNeeded()
  }

  async function onGroupConjunctionChange(groupId: string, conjunction: 'and' | 'or'): Promise<void> {
    const view = activeView.value
    if (!view) return
    const g = view.filterGroups.find((g) => g.id === groupId)
    if (!g) return
    g.conjunction = conjunction
    await saveViews()
    await onQueryNeeded()
  }

  // ── Filter management ───────────────────────────────────────────────────────

  async function addFilter(groupId: string): Promise<void> {
    const view = activeView.value
    if (!view) return
    const g = view.filterGroups.find((g) => g.id === groupId)
    if (!g) return
    const firstSchemaId = schemas.value[0]?.id ?? nameColKey
    g.filters.push({
      id: crypto.randomUUID(),
      schemaId: firstSchemaId,
      operator: getOperatorsForSchemaId(firstSchemaId, schemas.value, displayedEntries.value, nameColKey)[0],
      value: _defaultFilterValue(firstSchemaId),
    })
    await saveViews()
    await onQueryNeeded()
  }

  async function removeFilter(groupId: string, filterId: string): Promise<void> {
    const view = activeView.value
    if (!view) return
    const g = view.filterGroups.find((g) => g.id === groupId)
    if (!g) return
    g.filters = g.filters.filter((f) => f.id !== filterId)
    if (g.filters.length === 0) {
      view.filterGroups = view.filterGroups.filter((x) => x.id !== groupId)
    }
    await saveViews()
    await onQueryNeeded()
  }

  async function onFilterSchemaChange(groupId: string, filterId: string, newSchemaId: string): Promise<void> {
    const f = _findFilter(groupId, filterId)
    if (!f) return
    f.schemaId = newSchemaId
    const ops = getOperatorsForSchemaId(newSchemaId, schemas.value, displayedEntries.value, nameColKey)
    if (!ops.includes(f.operator)) f.operator = ops[0]
    f.value = _defaultFilterValue(newSchemaId)
    delete f.dateMode
    delete f.dateOffset
    await saveViews()
    await onQueryNeeded()
  }

  async function onFilterOperatorChange(groupId: string, filterId: string, newOperator: FilterOperator): Promise<void> {
    const f = _findFilter(groupId, filterId)
    if (!f) return
    f.operator = newOperator
    if (!filterNeedsValue(newOperator)) f.value = ''
    if (newOperator !== 'between') delete f.value2
    await saveViews()
    await onQueryNeeded()
  }

  async function onFilterValueChange(groupId: string, filterId: string, newValue: string): Promise<void> {
    const f = _findFilter(groupId, filterId)
    if (!f) return
    f.value = newValue
    await saveViews()
    await onQueryNeeded()
  }

  async function onFilterValue2Change(groupId: string, filterId: string, newValue: string): Promise<void> {
    const f = _findFilter(groupId, filterId)
    if (!f) return
    f.value2 = newValue
    await saveViews()
    await onQueryNeeded()
  }

  async function onFilterDateModeChange(groupId: string, filterId: string, mode: DateFilterMode): Promise<void> {
    const f = _findFilter(groupId, filterId)
    if (!f) return
    f.dateMode  = mode
    f.value     = ''
    f.dateOffset = 0
    await saveViews()
    await onQueryNeeded()
  }

  async function onFilterDateOffsetChange(groupId: string, filterId: string, offset: number): Promise<void> {
    const f = _findFilter(groupId, filterId)
    if (!f) return
    f.dateOffset = isNaN(offset) ? 0 : offset
    await saveViews()
    await onQueryNeeded()
  }

  // ── Public API ──────────────────────────────────────────────────────────────

  return {
    showFilterPanel,
    filterCount,
    relationFilterEntries,
    getRelationEntries,
    relationEntryTitle,
    // Group
    addGroup,
    removeGroup,
    onGroupConjunctionChange,
    // Filter
    addFilter,
    removeFilter,
    onFilterSchemaChange,
    onFilterOperatorChange,
    onFilterValueChange,
    onFilterValue2Change,
    onFilterDateModeChange,
    onFilterDateOffsetChange,
  }
}

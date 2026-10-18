<script setup lang="ts">
/**
 * FilterConditionRow
 *
 * Single source of truth for one filter condition's editing UI: the schema
 * picker (including the '__name__' title column), the operator picker, and the
 * type-aware value widget (text / number / checkbox / single-select /
 * multi-select / relation / date). Used by both the database view FilterPanel
 * and the automations action filter.
 *
 * The row is intentionally id-agnostic: it emits semantic changes only, and the
 * host wraps them with whatever group/filter identifiers it uses. All logic is
 * shared via the pure helpers in useFilterPanel; only the relation-entry
 * search (which is async and component-local) lives here.
 *
 * The value of a relation filter is picked from a server-side search of the
 * target database by title, as in the relation picker, so it reaches every
 * entry without loading the whole target database.
 *
 * Styling is supplied by the host via the *Class props (Variant A skinning), so
 * each host keeps its own visual identity while the structure and behaviour stay
 * unified. Defaults match the view FilterPanel's db__panel-* classes.
 */
import { ref, computed, watch, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  useDatabaseStore,
  ENTRY_SEARCH_LIMIT,
  type PropertySchema,
  type DatabaseEntry,
  type ViewFilter,
  type FilterOperator,
  type DateFilterMode,
} from '@/stores/database'
import {
  getOperatorsForSchemaId,
  getFilterSchema,
  filterNeedsValue,
  filterNeedsValue2,
  isDateFilter,
  isSelectFilter,
  isMultiSelectFilter,
  isCheckboxFilter,
  isRelationFilter,
  getSelectOptions,
} from '@/composables/useFilterPanel'
import DatePicker from '@/components/DatePicker.vue'

// ── Props / emits ─────────────────────────────────────────────────────────────

const props = withDefaults(defineProps<{
  filter:           ViewFilter
  schemas:          PropertySchema[]
  nameColKey:       string
  /** Custom label of the name column; the localized default when absent. */
  nameLabel?:       string | null
  displayedEntries?: DatabaseEntry[]
  /** Optional leading disabled option (used by the automations modal). */
  showPlaceholderOption?: boolean
  placeholderLabel?:      string
  // ── Skinning (defaults match the view FilterPanel) ──
  schemaSelectClass?:   string
  operatorSelectClass?: string
  valueSelectClass?:    string
  shortSelectClass?:    string
  valueInputClass?:     string
  numberInputClass?:    string
  narrowInputClass?:    string
}>(), {
  nameLabel:             null,
  displayedEntries:      () => [],
  showPlaceholderOption: false,
  placeholderLabel:      '',
  schemaSelectClass:     'db__panel-select',
  operatorSelectClass:   'db__panel-select',
  valueSelectClass:      'db__panel-select',
  shortSelectClass:      'db__panel-select db__panel-select--short',
  valueInputClass:       'db__panel-input',
  numberInputClass:      'db__panel-input',
  narrowInputClass:      'db__panel-input db__panel-input--narrow',
})

const emit = defineEmits<{
  (e: 'schema-change',      schemaId: string):    void
  (e: 'operator-change',    op: FilterOperator):  void
  (e: 'value-change',       value: string):       void
  (e: 'value2-change',      value: string):       void
  (e: 'date-mode-change',   mode: DateFilterMode): void
  (e: 'date-offset-change', offset: number):      void
}>()

// ── Dependencies ──────────────────────────────────────────────────────────────

const { t }   = useI18n()
const dbStore = useDatabaseStore()

// ── Relation value: server-side search ────────────────────────────────────────

/** Delay between the last keystroke and the search request. */
const RELATION_SEARCH_DEBOUNCE_MS = 250

const relationQuery   = ref('')
const relationResults = ref<DatabaseEntry[]>([])
const relationTotal   = ref(0)
let _relationTimer: ReturnType<typeof setTimeout> | null = null
let _relationSeq = 0

/** Target database of the relation this row filters on, if it is one. */
const relationTargetId = computed<string | null>(() => {
  if (!isRelationFilter(props.filter, props.schemas, props.nameColKey)) return null
  const schema = props.schemas.find((s) => s.id === props.filter.schemaId)
  return (schema?.config?.target_database_id as string | undefined) ?? null
})

async function runRelationSearch(): Promise<void> {
  const targetId = relationTargetId.value
  if (!targetId) return
  const seq = ++_relationSeq
  try {
    const result = await dbStore.searchEntries(targetId, relationQuery.value, ENTRY_SEARCH_LIMIT)
    if (seq !== _relationSeq) return
    relationResults.value = result.entries
    relationTotal.value   = result.total
  } catch {
    if (seq !== _relationSeq) return
    relationResults.value = []
    relationTotal.value   = 0
  }
}

function onRelationQueryInput(value: string): void {
  relationQuery.value = value
  if (_relationTimer) clearTimeout(_relationTimer)
  _relationTimer = setTimeout(() => {
    _relationTimer = null
    runRelationSearch()
  }, RELATION_SEARCH_DEBOUNCE_MS)
}

// A new relation property starts over with an empty search; the chosen
// entry's title is resolved so it shows even when the search does not list it.
watch(
  relationTargetId,
  (targetId) => {
    relationQuery.value   = ''
    relationResults.value = []
    relationTotal.value   = 0
    if (!targetId) return
    runRelationSearch()
    if (props.filter.value) dbStore.resolveEntryTitles(targetId, [props.filter.value])
  },
  { immediate: true },
)

onUnmounted(() => {
  if (_relationTimer) clearTimeout(_relationTimer)
})

/** True when the chosen entry is not among the listed results. */
const selectedOutsideResults = computed<boolean>(() =>
  !!props.filter.value && !relationResults.value.some((e) => e.id === props.filter.value),
)

const selectedTitle = computed<string>(() =>
  (dbStore.getRelationTitle(props.filter.value) ?? '').trim() || t('main.untitled'),
)

function entryTitle(entry: DatabaseEntry): string {
  return ((entry.content?.title as string | undefined) ?? '').trim() || t('main.untitled')
}

// ── Helpers threading props into the pure functions ───────────────────────────

// Formula columns infer their result type from entry values. When a filter
// empties the result set (e.g. an as-yet-incomplete 'between' range, or any
// filter that legitimately matches nothing), displayedEntries goes empty and
// the inference would collapse to 'text' — dropping date operators and breaking
// the just-selected operator. We therefore keep the last non-empty entries and
// infer from those, so the operator list and value widget stay stable.
const lastNonEmptyEntries = ref<DatabaseEntry[]>([])
watch(
  () => props.displayedEntries,
  (entries) => { if (entries && entries.length) lastNonEmptyEntries.value = entries },
  { immediate: true },
)
const effectiveEntries = computed<DatabaseEntry[]>(
  () => (props.displayedEntries.length ? props.displayedEntries : lastNonEmptyEntries.value),
)

function operators(schemaId: string): FilterOperator[] {
  return getOperatorsForSchemaId(schemaId, props.schemas, effectiveEntries.value, props.nameColKey)
}

function isNumberFilter(): boolean {
  return getFilterSchema(props.filter, props.schemas, props.nameColKey)?.type === 'number'
}
</script>

<template>
  <!-- Schema picker -->
  <select
    :class="schemaSelectClass"
    :value="filter.schemaId"
    @change="emit('schema-change', ($event.target as HTMLSelectElement).value)"
  >
    <option v-if="showPlaceholderOption" value="" disabled>{{ placeholderLabel }}</option>
    <option :value="nameColKey">{{ nameLabel || t('db.nameColumn') }}</option>
    <option v-for="s in schemas" :key="s.id" :value="s.id">{{ s.name }}</option>
  </select>

  <!-- Operator picker -->
  <select
    :class="operatorSelectClass"
    :value="filter.operator"
    @change="emit('operator-change', ($event.target as HTMLSelectElement).value as FilterOperator)"
  >
    <option v-for="op in operators(filter.schemaId)" :key="op" :value="op">
      {{ t(`db.filter.operators.${op}`) }}
    </option>
  </select>

  <!-- Value widget (varies by schema type) -->
  <template v-if="filterNeedsValue(filter.operator)">

    <!-- Date filter -->
    <template v-if="isDateFilter(filter, schemas, effectiveEntries, nameColKey)">
      <!-- 'between' shows two plain date pickers; no dateMode selector needed -->
      <template v-if="filterNeedsValue2(filter.operator)">
        <DatePicker
          :input-class="valueInputClass"
          :model-value="filter.value"
          @update:model-value="emit('value-change', $event)"
        />
        <DatePicker
          :input-class="valueInputClass"
          :model-value="filter.value2 ?? ''"
          @update:model-value="emit('value2-change', $event)"
        />
      </template>
      <!-- All other date operators use the dateMode selector -->
      <template v-else>
        <select
          :class="shortSelectClass"
          :value="filter.dateMode ?? 'exact'"
          @change="emit('date-mode-change', ($event.target as HTMLSelectElement).value as DateFilterMode)"
        >
          <option value="exact">{{ t('db.filter.dateModes.exact') }}</option>
          <option value="today">{{ t('db.filter.dateModes.today') }}</option>
          <option value="relative">{{ t('db.filter.dateModes.relative') }}</option>
        </select>
        <DatePicker
          v-if="(filter.dateMode ?? 'exact') === 'exact'"
          :input-class="valueInputClass"
          :model-value="filter.value"
          @update:model-value="emit('value-change', $event)"
        />
        <input
          v-else-if="filter.dateMode === 'relative'"
          type="number"
          :class="narrowInputClass"
          :value="filter.dateOffset ?? 0"
          :placeholder="t('db.filter.dateModes.offsetPlaceholder')"
          @input="emit('date-offset-change', Number(($event.target as HTMLInputElement).value))"
        />
      </template>
    </template>

    <!-- Checkbox filter -->
    <template v-else-if="isCheckboxFilter(filter, schemas, nameColKey)">
      <select
        :class="shortSelectClass"
        :value="filter.value"
        @change="emit('value-change', ($event.target as HTMLSelectElement).value)"
      >
        <option value="true">{{ t('db.filter.checkboxTrue') }}</option>
        <option value="false">{{ t('db.filter.checkboxFalse') }}</option>
      </select>
    </template>

    <!-- Single-select filter -->
    <template v-else-if="isSelectFilter(filter, schemas, nameColKey)">
      <select
        :class="valueSelectClass"
        :value="filter.value"
        @change="emit('value-change', ($event.target as HTMLSelectElement).value)"
      >
        <option value="">{{ t('db.filter.selectAny') }}</option>
        <option v-for="opt in getSelectOptions(filter, schemas, nameColKey)" :key="opt.id" :value="opt.id">
          {{ opt.label }}
        </option>
      </select>
    </template>

    <!-- Multi-select filter -->
    <template v-else-if="isMultiSelectFilter(filter, schemas, nameColKey)">
      <select
        :class="valueSelectClass"
        :value="filter.value"
        @change="emit('value-change', ($event.target as HTMLSelectElement).value)"
      >
        <option value="">{{ t('db.filter.selectAny') }}</option>
        <option v-for="opt in getSelectOptions(filter, schemas, nameColKey)" :key="opt.id" :value="opt.id">
          {{ opt.label }}
        </option>
      </select>
    </template>

    <!-- Relation filter -->
    <template v-else-if="isRelationFilter(filter, schemas, nameColKey)">
      <input
        type="text"
        :class="valueInputClass"
        :value="relationQuery"
        :placeholder="t('db.filter.searchEntry')"
        @input="onRelationQueryInput(($event.target as HTMLInputElement).value)"
      />
      <select
        :class="valueSelectClass"
        :value="filter.value"
        @change="emit('value-change', ($event.target as HTMLSelectElement).value)"
      >
        <option value="">{{ t('db.filter.selectAny') }}</option>
        <option v-if="selectedOutsideResults" :value="filter.value">{{ selectedTitle }}</option>
        <option
          v-for="entry in relationResults"
          :key="entry.id"
          :value="entry.id"
        >
          {{ entryTitle(entry) }}
        </option>
        <option v-if="relationTotal > relationResults.length" disabled value="">
          {{ t('db.relation.moreResults', { shown: relationResults.length, total: relationTotal }) }}
        </option>
      </select>
    </template>

    <!-- Number filter -->
    <template v-else-if="isNumberFilter()">
      <input
        type="number"
        :class="numberInputClass"
        :value="filter.value"
        :placeholder="t('db.filter.value')"
        @input="emit('value-change', ($event.target as HTMLInputElement).value)"
      />
    </template>

    <!-- Text / fallback filter -->
    <template v-else>
      <input
        type="text"
        :class="valueInputClass"
        :value="filter.value"
        :placeholder="t('db.filter.value')"
        @input="emit('value-change', ($event.target as HTMLInputElement).value)"
      />
    </template>
  </template>
</template>

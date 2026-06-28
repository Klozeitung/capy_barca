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
 * shared via the pure helpers in useFilterPanel; only the relation-entry cache
 * (which is async and component-local) lives here.
 *
 * Styling is supplied by the host via the *Class props (Variant A skinning), so
 * each host keeps its own visual identity while the structure and behaviour stay
 * unified. Defaults match the view FilterPanel's db__panel-* classes.
 */
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  useDatabaseStore,
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

// ── Props / emits ─────────────────────────────────────────────────────────────

const props = withDefaults(defineProps<{
  filter:           ViewFilter
  schemas:          PropertySchema[]
  nameColKey:       string
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

// ── Relation-entry cache (loaded lazily per schema) ───────────────────────────

const relationEntries = ref<Record<string, DatabaseEntry[]>>({})

function getRelationEntries(schemaId: string): DatabaseEntry[] {
  if (schemaId in relationEntries.value) return relationEntries.value[schemaId]
  const schema   = props.schemas.find((s) => s.id === schemaId)
  const targetId = schema?.config?.target_database_id as string | undefined
  if (!targetId) return []
  relationEntries.value[schemaId] = []
  dbStore.fetchEntries(targetId).then((entries) => {
    relationEntries.value[schemaId] = entries
  })
  return []
}

function entryTitle(entry: DatabaseEntry): string {
  return ((entry.content?.title as string | undefined) ?? '').trim() || t('main.untitled')
}

// ── Helpers threading props into the pure functions ───────────────────────────

function operators(schemaId: string): FilterOperator[] {
  return getOperatorsForSchemaId(schemaId, props.schemas, props.displayedEntries, props.nameColKey)
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
    <option :value="nameColKey">{{ t('db.nameColumn') }}</option>
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
    <template v-if="isDateFilter(filter, schemas, displayedEntries, nameColKey)">
      <!-- 'between' shows two plain date pickers; no dateMode selector needed -->
      <template v-if="filterNeedsValue2(filter.operator)">
        <input
          type="date"
          :class="valueInputClass"
          :value="filter.value"
          @input="emit('value-change', ($event.target as HTMLInputElement).value)"
        />
        <input
          type="date"
          :class="valueInputClass"
          :value="filter.value2 ?? ''"
          @input="emit('value2-change', ($event.target as HTMLInputElement).value)"
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
        <input
          v-if="(filter.dateMode ?? 'exact') === 'exact'"
          type="date"
          :class="valueInputClass"
          :value="filter.value"
          @input="emit('value-change', ($event.target as HTMLInputElement).value)"
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
        <option v-for="opt in getSelectOptions(filter, schemas, nameColKey)" :key="opt" :value="opt">
          {{ opt }}
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
        <option v-for="opt in getSelectOptions(filter, schemas, nameColKey)" :key="opt" :value="opt">
          {{ opt }}
        </option>
      </select>
    </template>

    <!-- Relation filter -->
    <template v-else-if="isRelationFilter(filter, schemas, nameColKey)">
      <select
        :class="valueSelectClass"
        :value="filter.value"
        @change="emit('value-change', ($event.target as HTMLSelectElement).value)"
      >
        <option value="">{{ t('db.filter.selectAny') }}</option>
        <option
          v-for="entry in getRelationEntries(filter.schemaId)"
          :key="entry.id"
          :value="entry.id"
        >
          {{ entryTitle(entry) }}
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

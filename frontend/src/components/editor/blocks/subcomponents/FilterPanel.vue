<script setup lang="ts">
/**
 * FilterPanel
 *
 * Renders the filter-group UI panel.  All data is received via props; every
 * mutation is emitted so the parent (DatabaseBlock) can delegate to the
 * useFilterPanel composable.
 *
 * Pure helper functions (getOperatorsForSchemaId, isDateFilter, …) are
 * imported directly from useFilterPanel to avoid duplicating logic.
 */
import { ref } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { useDatabaseStore, type PropertySchema, type DatabaseEntry, type DatabaseView, type FilterOperator, type DateFilterMode } from '@/stores/database'
import {
  getOperatorsForSchemaId,
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

const props = defineProps<{
  activeView:       DatabaseView | null
  schemas:          PropertySchema[]
  displayedEntries: DatabaseEntry[]
  nameColKey:       string
}>()

const emit = defineEmits<{
  (e: 'group-conjunction-change', groupId: string, conjunction: 'and' | 'or'): void
  (e: 'remove-group',             groupId: string):                             void
  (e: 'filter-schema-change',     groupId: string, filterId: string, schemaId: string):    void
  (e: 'filter-operator-change',   groupId: string, filterId: string, op: FilterOperator):  void
  (e: 'filter-value-change',      groupId: string, filterId: string, value: string):       void
  (e: 'filter-value2-change',     groupId: string, filterId: string, value: string):       void
  (e: 'filter-date-mode-change',  groupId: string, filterId: string, mode: DateFilterMode): void
  (e: 'filter-date-offset-change',groupId: string, filterId: string, offset: number):      void
  (e: 'remove-filter',            groupId: string, filterId: string):                      void
  (e: 'add-filter',               groupId: string):                             void
  (e: 'add-group'):                                                             void
}>()

// ── Dependencies ──────────────────────────────────────────────────────────────

const { t }     = useI18n()
const dbStore   = useDatabaseStore()

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

// ── Wrappers that thread props into the pure helpers ──────────────────────────

function operators(schemaId: string): FilterOperator[] {
  return getOperatorsForSchemaId(schemaId, props.schemas, props.displayedEntries, props.nameColKey)
}
</script>

<template>
  <div class="db__panel db__panel--filters" @click.stop>
    <p v-if="!activeView?.filterGroups.length" class="db__panel-empty">
      {{ t('db.filter.noFilters') }}
    </p>

    <!-- Filter groups -->
    <div
      v-for="group in activeView?.filterGroups"
      :key="group.id"
      class="db__filter-group"
    >
      <!-- Group header: conjunction toggle + remove -->
      <div class="db__filter-group-header">
        <select
          class="db__panel-select db__panel-select--conjunction"
          :value="group.conjunction"
          @change="emit('group-conjunction-change', group.id, ($event.target as HTMLSelectElement).value as 'and' | 'or')"
        >
          <option value="and">{{ t('db.filter.conjunctionAll') }}</option>
          <option value="or">{{ t('db.filter.conjunctionAny') }}</option>
        </select>
        <button class="db__panel-remove db__filter-group-remove" @click="emit('remove-group', group.id)">
          <Icon icon="mdi:close" width="13" height="13" />
        </button>
      </div>

      <!-- Individual filters -->
      <div
        v-for="filter in group.filters"
        :key="filter.id"
        class="db__panel-row db__panel-row--indented"
      >
        <!-- Schema picker -->
        <select
          class="db__panel-select"
          :value="filter.schemaId"
          @change="emit('filter-schema-change', group.id, filter.id, ($event.target as HTMLSelectElement).value)"
        >
          <option :value="nameColKey">{{ t('db.nameColumn') }}</option>
          <option v-for="s in schemas" :key="s.id" :value="s.id">{{ s.name }}</option>
        </select>

        <!-- Operator picker -->
        <select
          class="db__panel-select"
          :value="filter.operator"
          @change="emit('filter-operator-change', group.id, filter.id, ($event.target as HTMLSelectElement).value as FilterOperator)"
        >
          <option v-for="op in operators(filter.schemaId)" :key="op" :value="op">
            {{ t(`db.filter.operators.${op}`) }}
          </option>
        </select>

        <!-- Value inputs (varies by schema type) -->
        <template v-if="filterNeedsValue(filter.operator)">

          <!-- Date filter -->
          <template v-if="isDateFilter(filter, schemas, displayedEntries, nameColKey)">
            <!-- 'between' shows two plain date pickers; no dateMode selector needed -->
            <template v-if="filterNeedsValue2(filter.operator)">
              <input
                type="date"
                class="db__panel-input"
                :value="filter.value"
                @input="emit('filter-value-change', group.id, filter.id, ($event.target as HTMLInputElement).value)"
              />
              <input
                type="date"
                class="db__panel-input"
                :value="filter.value2 ?? ''"
                @input="emit('filter-value2-change', group.id, filter.id, ($event.target as HTMLInputElement).value)"
              />
            </template>
            <!-- All other date operators use the existing dateMode selector -->
            <template v-else>
              <select
                class="db__panel-select db__panel-select--short"
                :value="filter.dateMode ?? 'exact'"
                @change="emit('filter-date-mode-change', group.id, filter.id, ($event.target as HTMLSelectElement).value as DateFilterMode)"
              >
                <option value="exact">{{ t('db.filter.dateModes.exact') }}</option>
                <option value="today">{{ t('db.filter.dateModes.today') }}</option>
                <option value="relative">{{ t('db.filter.dateModes.relative') }}</option>
              </select>
              <input
                v-if="(filter.dateMode ?? 'exact') === 'exact'"
                type="date"
                class="db__panel-input"
                :value="filter.value"
                @input="emit('filter-value-change', group.id, filter.id, ($event.target as HTMLInputElement).value)"
              />
              <input
                v-else-if="filter.dateMode === 'relative'"
                type="number"
                class="db__panel-input db__panel-input--narrow"
                :value="filter.dateOffset ?? 0"
                :placeholder="t('db.filter.dateModes.offsetPlaceholder')"
                @input="emit('filter-date-offset-change', group.id, filter.id, Number(($event.target as HTMLInputElement).value))"
              />
            </template>
          </template>

          <!-- Checkbox filter -->
          <template v-else-if="isCheckboxFilter(filter, schemas, nameColKey)">
            <select
              class="db__panel-select db__panel-select--short"
              :value="filter.value"
              @change="emit('filter-value-change', group.id, filter.id, ($event.target as HTMLSelectElement).value)"
            >
              <option value="true">{{ t('db.filter.checkboxTrue') }}</option>
              <option value="false">{{ t('db.filter.checkboxFalse') }}</option>
            </select>
          </template>

          <!-- Single-select filter -->
          <template v-else-if="isSelectFilter(filter, schemas, nameColKey)">
            <select
              class="db__panel-select"
              :value="filter.value"
              @change="emit('filter-value-change', group.id, filter.id, ($event.target as HTMLSelectElement).value)"
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
              class="db__panel-select"
              :value="filter.value"
              @change="emit('filter-value-change', group.id, filter.id, ($event.target as HTMLSelectElement).value)"
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
              class="db__panel-select"
              :value="filter.value"
              @change="emit('filter-value-change', group.id, filter.id, ($event.target as HTMLSelectElement).value)"
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

          <!-- Text / fallback filter -->
          <template v-else>
            <input
              class="db__panel-input"
              :value="filter.value"
              :placeholder="t('db.filter.value')"
              @input="emit('filter-value-change', group.id, filter.id, ($event.target as HTMLInputElement).value)"
            />
          </template>
        </template>

        <button class="db__panel-remove" @click="emit('remove-filter', group.id, filter.id)">
          <Icon icon="mdi:close" width="13" height="13" />
        </button>
      </div>

      <!-- Add filter within group -->
      <button class="db__panel-add-btn db__panel-add-btn--indented" @click="emit('add-filter', group.id)">
        <Icon icon="mdi:plus" width="13" height="13" />
        {{ t('db.filter.addFilter') }}
      </button>
    </div>

    <!-- Add group -->
    <button class="db__panel-add-btn db__panel-add-btn--group" @click="emit('add-group')">
      <Icon icon="mdi:plus-box-outline" width="13" height="13" />
      {{ t('db.filter.addGroup') }}
    </button>
  </div>
</template>

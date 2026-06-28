<script setup lang="ts">
/**
 * FilterPanel
 *
 * Renders the filter-group UI panel: group chrome (conjunction toggle, add /
 * remove group, add / remove filter) plus one FilterConditionRow per filter.
 * All condition-editing logic and the type-aware value widgets live in the
 * shared FilterConditionRow component so the view filter and the automations
 * action filter stay in sync.
 *
 * All data is received via props; every mutation is emitted so the parent
 * (DatabaseBlock) can delegate to the useFilterPanel composable.
 */
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { type PropertySchema, type DatabaseEntry, type DatabaseView, type FilterOperator, type DateFilterMode } from '@/stores/database'
import FilterConditionRow from './FilterConditionRow.vue'

// ── Props / emits ─────────────────────────────────────────────────────────────

defineProps<{
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

const { t } = useI18n()
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
        <FilterConditionRow
          :filter="filter"
          :schemas="schemas"
          :displayed-entries="displayedEntries"
          :name-col-key="nameColKey"
          @schema-change="(v: string) => emit('filter-schema-change', group.id, filter.id, v)"
          @operator-change="(v: FilterOperator) => emit('filter-operator-change', group.id, filter.id, v)"
          @value-change="(v: string) => emit('filter-value-change', group.id, filter.id, v)"
          @value2-change="(v: string) => emit('filter-value2-change', group.id, filter.id, v)"
          @date-mode-change="(v: DateFilterMode) => emit('filter-date-mode-change', group.id, filter.id, v)"
          @date-offset-change="(v: number) => emit('filter-date-offset-change', group.id, filter.id, v)"
        />

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

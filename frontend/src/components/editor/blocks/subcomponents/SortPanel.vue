<script setup lang="ts">
/**
 * SortPanel
 *
 * Renders the sort-rule UI panel.  Purely presentational – all mutations are
 * emitted to the parent (DatabaseBlock) which delegates to useViewManager.
 */
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import type { PropertySchema, DatabaseView } from '@/stores/database'

// ── Props / emits ─────────────────────────────────────────────────────────────

defineProps<{
  activeView: DatabaseView | null
  schemas:    PropertySchema[]
  nameColKey: string
}>()

const emit = defineEmits<{
  (e: 'add-sort'):                                             void
  (e: 'remove-sort',         sortId: string):                 void
  (e: 'sort-schema-change',  sortId: string, schemaId: string): void
  (e: 'sort-direction-change', sortId: string, dir: 'asc' | 'desc'): void
}>()

const { t } = useI18n()
</script>

<template>
  <div class="db__panel" @click.stop>
    <p v-if="!activeView?.sorts.length" class="db__panel-empty">
      {{ t('db.sort.noSorts') }}
    </p>

    <div v-for="sort in activeView?.sorts" :key="sort.id" class="db__panel-row">
      <select
        class="db__panel-select"
        :value="sort.schemaId"
        @change="emit('sort-schema-change', sort.id, ($event.target as HTMLSelectElement).value)"
      >
        <option :value="nameColKey">{{ t('db.nameColumn') }}</option>
        <option v-for="s in schemas" :key="s.id" :value="s.id">{{ s.name }}</option>
      </select>

      <select
        class="db__panel-select db__panel-select--short"
        :value="sort.direction"
        @change="emit('sort-direction-change', sort.id, ($event.target as HTMLSelectElement).value as 'asc' | 'desc')"
      >
        <option value="asc">{{ t('db.sort.asc') }}</option>
        <option value="desc">{{ t('db.sort.desc') }}</option>
      </select>

      <button class="db__panel-remove" @click="emit('remove-sort', sort.id)">
        <Icon icon="mdi:close" width="13" height="13" />
      </button>
    </div>

    <button class="db__panel-add-btn" @click="emit('add-sort')">
      <Icon icon="mdi:plus" width="13" height="13" />
      {{ t('db.sort.addSort') }}
    </button>
  </div>
</template>

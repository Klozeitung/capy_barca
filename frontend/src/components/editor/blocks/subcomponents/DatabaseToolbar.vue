<script lang="ts">
/** What the export menu offers; calendar views export to ICS only. */
export type ExportFormat = 'csv' | 'excel' | 'pdf' | 'ics'
</script>

<script setup lang="ts">
/**
 * DatabaseToolbar
 *
 * The toolbar of a database block.
 *
 * Left: filter, sort, view settings, automations and templates. They change
 * the database or its views and are offered with write access only
 * (``useEditable``); an inline database the user may only read shows the
 * read-only badge instead.
 *
 * Right: the name search, the new-entry button (table views) and the export
 * menu.
 *
 * At most one of the filter, sort, export and template panels is open; opening
 * one closes the others. The open state is owned by the parent (v-model), which
 * also closes every panel on a click outside. The filter and sort panels are
 * passed in as slots, so their events stay with the parent's composables.
 *
 * The name search expands from a magnifier into a text field; an empty field
 * collapses again when it loses the focus. The query is a v-model, the parent
 * runs the search.
 *
 * Styles live in DatabaseBlock.css and DatabaseBlock.vue.
 */
import { nextTick, ref } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { useEditable } from '@/composables/useEditable'
import ReadOnlyBadge from '@/components/main/ReadOnlyBadge.vue'
import TemplateManagerPanel from './TemplateManagerPanel.vue'

defineProps<{
  databaseId: string
  /** Rendered inside a content block rather than on a page of its own. */
  inline?: boolean
  filterCount: number
  sortCount: number
  /** Calendar views export to ICS only. */
  isCalendarView: boolean
  /** Whether the new-entry button applies (table views). */
  showNewEntry: boolean
  /** More entries exist than are loaded; the new-entry hint says so. */
  hasMore: boolean
  isAddingRow: boolean
}>()

const showFilterPanel      = defineModel<boolean>('showFilterPanel', { required: true })
const showSortPanel        = defineModel<boolean>('showSortPanel', { required: true })
const showExportMenu       = defineModel<boolean>('showExportMenu', { required: true })
const showTemplatesModal   = defineModel<boolean>('showTemplatesModal', { required: true })
const showAutomationsModal = defineModel<boolean>('showAutomationsModal', { required: true })
const searchQuery          = defineModel<string>('searchQuery', { required: true })

const emit = defineEmits<{
  (e: 'open-view-settings', event: MouseEvent): void
  (e: 'add-row'): void
  (e: 'export', format: ExportFormat): void
  (e: 'edit-template', templateId: string): void
  (e: 'show-tip', event: MouseEvent, text: string): void
  (e: 'hide-tip'): void
}>()

defineSlots<{
  'filter-panel'(): unknown
  'sort-panel'(): unknown
}>()

const { t } = useI18n()
const editable = useEditable()

// ── Panels ────────────────────────────────────────────────────────────────────

function toggleFilterPanel(): void {
  showFilterPanel.value = !showFilterPanel.value
  showSortPanel.value = false
  showExportMenu.value = false
  showTemplatesModal.value = false
}

function toggleSortPanel(): void {
  showSortPanel.value = !showSortPanel.value
  showFilterPanel.value = false
  showExportMenu.value = false
  showTemplatesModal.value = false
}

function openViewSettings(event: MouseEvent): void {
  showTemplatesModal.value = false
  emit('open-view-settings', event)
}

function toggleAutomations(): void {
  showAutomationsModal.value = !showAutomationsModal.value
  showFilterPanel.value = false
  showSortPanel.value = false
  showExportMenu.value = false
  showTemplatesModal.value = false
}

function toggleTemplates(): void {
  showTemplatesModal.value = !showTemplatesModal.value
  showFilterPanel.value = false
  showSortPanel.value = false
  showExportMenu.value = false
  showAutomationsModal.value = false
}

function editTemplate(templateId: string): void {
  emit('edit-template', templateId)
  showTemplatesModal.value = false
}

function toggleExportMenu(): void {
  emit('hide-tip')
  showExportMenu.value = !showExportMenu.value
  showFilterPanel.value = false
  showSortPanel.value = false
  showTemplatesModal.value = false
}

function addRow(): void {
  emit('hide-tip')
  showTemplatesModal.value = false
  emit('add-row')
}

// ── Name search ───────────────────────────────────────────────────────────────

const searchActive  = ref(false)
const searchInputEl = ref<HTMLInputElement | null>(null)

function toggleNameSearch(): void {
  searchActive.value = !searchActive.value
  if (searchActive.value) {
    nextTick(() => searchInputEl.value?.focus())
  } else {
    searchQuery.value = ''
  }
}

function openNameSearch(): void {
  emit('hide-tip')
  showTemplatesModal.value = false
  toggleNameSearch()
}

function clearNameSearch(): void {
  searchQuery.value = ''
  nextTick(() => searchInputEl.value?.focus())
}

function onSearchBlur(): void {
  // An empty field collapses so it does not linger.
  if (!searchQuery.value.trim()) searchActive.value = false
}
</script>

<template>
  <div class="db__toolbar" @click.stop>
    <div class="db__toolbar-left">

      <ReadOnlyBadge v-if="!editable && inline" />

      <template v-if="editable">

      <!-- Filter -->
      <div class="db__toolbar-item">
        <button
          class="db__toolbar-btn"
          :class="{ 'db__toolbar-btn--active': showFilterPanel || filterCount > 0 }"
          @click.stop="toggleFilterPanel"
        >
          <Icon icon="mdi:filter-outline" width="14" height="14" />
          {{ t('db.filter.title') }}
          <span v-if="filterCount > 0" class="db__toolbar-badge">{{ filterCount }}</span>
        </button>
        <slot v-if="showFilterPanel" name="filter-panel" />
      </div>

      <!-- Sort -->
      <div class="db__toolbar-item">
        <button
          class="db__toolbar-btn"
          :class="{ 'db__toolbar-btn--active': showSortPanel || sortCount > 0 }"
          @click.stop="toggleSortPanel"
        >
          <Icon icon="mdi:sort" width="14" height="14" />
          {{ t('db.sort.title') }}
          <span v-if="sortCount > 0" class="db__toolbar-badge">{{ sortCount }}</span>
        </button>
        <slot v-if="showSortPanel" name="sort-panel" />
      </div>

      <!-- View settings -->
      <div class="db__toolbar-item">
        <button
          class="db__toolbar-btn"
          :title="t('db.viewSettings.title')"
          @click.stop="openViewSettings"
        >
          <Icon icon="mdi:cog-outline" width="14" height="14" />
          {{ t('db.viewSettings.title') }}
        </button>
      </div>

      <!-- Automations -->
      <div class="db__toolbar-item">
        <button
          class="db__toolbar-btn"
          :class="{ 'db__toolbar-btn--active': showAutomationsModal }"
          :title="t('automations.title')"
          @click.stop="toggleAutomations"
        >
          <Icon icon="mdi:lightning-bolt-outline" width="14" height="14" />
          {{ t('automations.title') }}
        </button>
      </div>

      <!-- Templates -->
      <div class="db__toolbar-item">
        <button
          class="db__toolbar-btn"
          :class="{ 'db__toolbar-btn--active': showTemplatesModal }"
          :title="t('db.templates.manage')"
          @click.stop="toggleTemplates"
        >
          <Icon icon="mdi:file-document-multiple-outline" width="14" height="14" />
          {{ t('db.templates.title') }}
        </button>
        <div v-if="showTemplatesModal" class="db__panel db__panel--template-manager" @click.stop>
          <TemplateManagerPanel
            :database-id="databaseId"
            @edit-template="editTemplate"
            @close="showTemplatesModal = false"
          />
        </div>
      </div>

      </template>

    </div>

    <div class="db__toolbar-right">
      <!-- Name search -->
      <div class="db__toolbar-item">
        <button
          v-if="!searchActive"
          class="db__toolbar-btn db__toolbar-btn--icon"
          @mouseenter="emit('show-tip', $event, t('db.search.open'))"
          @mouseleave="emit('hide-tip')"
          @click.stop="openNameSearch"
        >
          <Icon icon="mdi:magnify" width="14" height="14" />
        </button>
        <div v-else class="db__search-field">
          <Icon icon="mdi:magnify" width="14" height="14" class="db__search-icon" />
          <input
            ref="searchInputEl"
            v-model="searchQuery"
            type="text"
            class="db__search-input"
            :placeholder="t('db.search.placeholder')"
            @keydown.escape="toggleNameSearch()"
            @blur="onSearchBlur"
          />
          <button
            class="db__search-clear"
            :title="t('db.search.clear')"
            @mousedown.prevent
            @click.stop="clearNameSearch()"
          >
            <Icon icon="mdi:close" width="13" height="13" />
          </button>
        </div>
      </div>

      <!-- New entry (table view only) -->
      <div v-if="editable && showNewEntry" class="db__toolbar-item">
        <button
          class="db__toolbar-btn db__toolbar-btn--new-entry db__toolbar-btn--icon"
          :disabled="isAddingRow"
          @mouseenter="emit('show-tip', $event, hasMore ? t('db.addRowLimitHint') : t('db.addRow'))"
          @mouseleave="emit('hide-tip')"
          @click.stop="addRow"
        >
          <Icon icon="mdi:plus" width="14" height="14" />
        </button>
      </div>

      <!-- Export -->
      <div class="db__toolbar-item">
        <button
          class="db__toolbar-btn db__toolbar-btn--icon"
          @mouseenter="emit('show-tip', $event, t('db.export.title'))"
          @mouseleave="emit('hide-tip')"
          @click.stop="toggleExportMenu"
        >
          <Icon icon="mdi:tray-arrow-up" width="14" height="14" />
        </button>
        <div v-if="showExportMenu" class="db__panel db__panel--right" @click.stop>
          <template v-if="isCalendarView">
            <button class="db__panel-action" @click="emit('export', 'ics')">
              <Icon icon="mdi:calendar-export-outline" width="14" height="14" />
              {{ t('db.export.ics') }}
            </button>
          </template>
          <template v-else>
            <button class="db__panel-action" @click="emit('export', 'csv')">
              <Icon icon="mdi:file-delimited-outline" width="14" height="14" />
              {{ t('db.export.csv') }}
            </button>
            <button class="db__panel-action" @click="emit('export', 'excel')">
              <Icon icon="mdi:microsoft-excel" width="14" height="14" />
              {{ t('db.export.excel') }}
            </button>
            <button class="db__panel-action" @click="emit('export', 'pdf')">
              <Icon icon="mdi:file-pdf-box" width="14" height="14" />
              {{ t('db.export.pdf') }}
            </button>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

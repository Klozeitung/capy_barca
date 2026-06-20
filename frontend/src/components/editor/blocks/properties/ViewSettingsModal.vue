<script setup lang="ts">
/**
 * ViewSettingsModal
 *
 * Two-column settings modal for a single database view.
 * Sections:
 *  - viewType   : switch between table / calendar and configure type-specific options
 *  - properties : visibility toggles per column
 *  - grouping   : (table view only) group-by property + per-column aggregation functions
 *  - headers    : (table view only) sticky column header + frozen leftmost columns
 *
 * Layout mirrors the global settings modal: a narrow sidebar on the left
 * with section navigation, and a content pane on the right.
 *
 * The component receives the full view object and all schemas, emits
 * ``update`` with a modified copy whenever the user changes a setting, and
 * emits ``close`` when the user dismisses it.  Persistence is handled by
 * the parent (DatabaseBlock).
 *
 * Upgrade path: add more sidebar entries (filter presets, sort presets, …)
 * by extending ``SECTIONS`` and adding a matching ``<template>`` branch.
 */
import { ref, computed } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import type { DatabaseView, PropertySchema, ViewType } from '@/stores/database'
import { useDatabaseStore, clampFrozenColumns, MAX_FROZEN_COLUMNS } from '@/stores/database'
import { getPropertyTypeIcon, isReadonlyPropertyType } from '@/stores/propertyTypes'

// ── Props / emits ─────────────────────────────────────────────────────────────

const props = defineProps<{
  view: DatabaseView
  schemas: PropertySchema[]
  databaseId: string
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'update', view: DatabaseView): void
}>()

// ── Dependencies ──────────────────────────────────────────────────────────────

const { t } = useI18n()
const dbStore = useDatabaseStore()

// ── Sidebar sections ──────────────────────────────────────────────────────────

const SECTIONS = [
  { key: 'viewType',   labelKey: 'db.viewSettings.sectionViewType',   icon: 'mdi:view-dashboard-outline' },
  { key: 'properties', labelKey: 'db.viewSettings.sectionProperties', icon: 'mdi:eye-outline' },
  { key: 'grouping',   labelKey: 'db.viewSettings.sectionGrouping',   icon: 'mdi:group' },
  { key: 'headers',    labelKey: 'db.viewSettings.sectionHeaders',    icon: 'mdi:pin-outline' },
] as const

type SectionKey = typeof SECTIONS[number]['key']
const activeSection = ref<SectionKey>('viewType')

// ── View type definitions ─────────────────────────────────────────────────────

interface ViewTypeDef {
  key: ViewType
  icon: string
  labelKey: string
  planned: boolean
}

const VIEW_TYPES: ViewTypeDef[] = [
  { key: 'table',       icon: 'mdi:table-large',            labelKey: 'db.viewSettings.viewTypes.table',       planned: false },
  { key: 'calendar',    icon: 'mdi:calendar-month-outline', labelKey: 'db.viewSettings.viewTypes.calendar',    planned: false },
  { key: 'list',        icon: 'mdi:format-list-bulleted',   labelKey: 'db.viewSettings.viewTypes.list',        planned: true  },
  { key: 'gallery',     icon: 'mdi:view-grid-outline',      labelKey: 'db.viewSettings.viewTypes.gallery',     planned: true  },
  { key: 'board',       icon: 'mdi:view-column-outline',    labelKey: 'db.viewSettings.viewTypes.board',       planned: true  },
  { key: 'family_tree', icon: 'mdi:file-tree-outline',      labelKey: 'db.viewSettings.viewTypes.family_tree', planned: true  },
  { key: 'mindmap',     icon: 'mdi:graph-outline',          labelKey: 'db.viewSettings.viewTypes.mindmap',     planned: true  },
  { key: 'graph',       icon: 'mdi:graph',                  labelKey: 'db.viewSettings.viewTypes.graph',       planned: true  },
]

// ── Local mutable state ───────────────────────────────────────────────────────

const hidden                  = ref<Set<string>>(new Set(props.view.hiddenColumns ?? []))
const localViewType           = ref<ViewType>(props.view.viewType ?? 'table')
const localCalendarSchemaId   = ref<string>(props.view.calendarDateSchemaId ?? '')
const localCalendarSubtype    = ref<'standard' | 'agenda'>(props.view.calendarSubtype ?? 'standard')
const localGroupBySchemaId    = ref<string>(props.view.groupBySchemaId ?? '')
const localStickyHeader       = ref<boolean>(props.view.stickyHeader ?? true)
const localFrozenColumns      = ref<number>(clampFrozenColumns(props.view.frozenColumns))

// ── Date schemas (for calendar property picker) ───────────────────────────────

const dateSchemas = computed(() =>
  props.schemas.filter(s => s.type === 'date' || s.type === 'created_time' || s.type === 'last_edited_time'),
)

// ── Create date property ──────────────────────────────────────────────────────

const isCreatingDateProp = ref(false)

async function createDateProperty(): Promise<void> {
  if (isCreatingDateProp.value) return
  isCreatingDateProp.value = true
  try {
    const created = await dbStore.createSchema(props.databaseId, {
      name: t('db.viewSettings.calendarCreateDateName'),
      type: 'date',
      // hasEndDate and includeTime are required for calendar to function correctly
      config: { hasEndDate: true, includeTime: true },
      group: 'Standard',
    })
    localCalendarSchemaId.value = created.id
    emitUpdate()
  } finally {
    isCreatingDateProp.value = false
  }
}

/**
 * Ensure the selected date schema has hasEndDate and includeTime enabled.
 * These flags are required for the calendar view to work correctly
 * (start/end storage, time-based sorting). Applied silently.
 */
async function ensureCalendarSchemaConfig(schemaId: string): Promise<void> {
  const schema = props.schemas.find(s => s.id === schemaId)
  if (!schema || schema.type !== 'date') return
  const cfg = schema.config ?? {}
  if (cfg.hasEndDate && cfg.includeTime) return  // already correct
  await dbStore.updateSchema(props.databaseId, schemaId, {
    config: { ...cfg, hasEndDate: true, includeTime: true },
  })
}

function setCalendarSchema(id: string): void {
  localCalendarSchemaId.value = id
  emitUpdate()
  if (id) ensureCalendarSchemaConfig(id)
}

function setCalendarSubtype(sub: 'standard' | 'agenda'): void {
  localCalendarSubtype.value = sub
  emitUpdate()
}

// ── Grouping helpers ──────────────────────────────────────────────────────────

/**
 * Schema types that cannot be used as a group-by property:
 * system/readonly types, computed types, and multi-value types that
 * don't have a single stable string key.
 */
const SKIP_GROUP_TYPES = new Set([
  'id', 'created_by', 'created_time', 'last_edited_by', 'last_edited_time',
  'formula', 'rollup', 'file', 'relation',
])

const groupableSchemas = computed(() =>
  props.schemas.filter(s => !SKIP_GROUP_TYPES.has(s.type)),
)

function setGroupBy(schemaId: string): void {
  localGroupBySchemaId.value = schemaId
  emitUpdate()
}

// ── Emit helpers ──────────────────────────────────────────────────────────────

function emitUpdate(): void {
  emit('update', {
    ...props.view,
    viewType: localViewType.value,
    hiddenColumns: [...hidden.value],
    calendarDateSchemaId: localCalendarSchemaId.value || undefined,
    calendarSubtype: localCalendarSubtype.value,
    groupBySchemaId: localGroupBySchemaId.value || undefined,
    stickyHeader: localStickyHeader.value,
    frozenColumns: localFrozenColumns.value,
  })
}

function setStickyHeader(value: boolean): void {
  localStickyHeader.value = value
  emitUpdate()
}

function setFrozenColumns(count: number): void {
  localFrozenColumns.value = clampFrozenColumns(count)
  emitUpdate()
}

/** Selectable frozen-column counts: 0..MAX_FROZEN_COLUMNS. */
const frozenColumnOptions = Array.from({ length: MAX_FROZEN_COLUMNS + 1 }, (_, i) => i)

function setViewType(type: ViewType): void {
  if (type === localViewType.value) return
  localViewType.value = type
  emitUpdate()
}

// ── Working copy of hiddenColumns ─────────────────────────────────────────────

// Name column is always visible — no toggle for it.
const NAME_COL_KEY = '__name__'

const allColumns = computed(() => [
  { key: NAME_COL_KEY, name: t('db.nameColumn'), icon: 'mdi:text', isReadonly: false, isName: true },
  ...props.schemas.map(s => ({
    key: s.id,
    name: s.name,
    icon: getPropertyTypeIcon(s.type),
    isReadonly: isReadonlyPropertyType(s.type),
    isName: false,
  })),
])

function isVisible(key: string): boolean {
  return !hidden.value.has(key)
}

function toggleColumn(key: string) {
  const updated = new Set(hidden.value)
  if (updated.has(key)) {
    updated.delete(key)
  } else {
    updated.add(key)
  }
  hidden.value = updated
  emitUpdate()
}

function showAll() {
  hidden.value = new Set()
  emitUpdate()
}

function hideAllReadonly() {
  const updated = new Set(hidden.value)
  for (const col of allColumns.value) {
    if (col.isReadonly) updated.add(col.key)
  }
  hidden.value = updated
  emitUpdate()
}

function hideAll() {
  // Hide every toggleable column. The name column has no toggle and is always
  // visible, so it is never added to the hidden set.
  const updated = new Set(hidden.value)
  for (const col of allColumns.value) {
    if (!col.isName) updated.add(col.key)
  }
  hidden.value = updated
  emitUpdate()
}
</script>

<template>
  <div class="vsm-backdrop" @mousedown.self="emit('close')">
    <div class="vsm" role="dialog" :aria-label="t('db.viewSettings.title')">

      <!-- ── Header ──────────────────────────────────────────────────────── -->
      <div class="vsm__header">
        <span class="vsm__header-title">
          <Icon icon="mdi:tune-variant" width="15" height="15" />
          {{ t('db.viewSettings.title') }}
          <span class="vsm__header-view-name">— {{ view.name }}</span>
        </span>
        <button class="vsm__close" @click="emit('close')" :aria-label="t('actions.cancel')">
          <Icon icon="mdi:close" width="15" height="15" />
        </button>
      </div>

      <!-- ── Two-column body ─────────────────────────────────────────────── -->
      <div class="vsm__body">

        <!-- Sidebar -->
        <nav class="vsm__sidebar">
          <button
            v-for="section in SECTIONS.filter(s => (s.key !== 'grouping' && s.key !== 'headers') || localViewType === 'table')"
            :key="section.key"
            class="vsm__nav-item"
            :class="{ 'vsm__nav-item--active': activeSection === section.key }"
            @click="activeSection = section.key"
          >
            <Icon :icon="section.icon" width="14" height="14" />
            {{ t(section.labelKey) }}
          </button>
        </nav>

        <!-- Content -->
        <div class="vsm__content">

          <!-- ── Section: View type ───────────────────────────────────────── -->
          <template v-if="activeSection === 'viewType'">
            <div class="vsm__section-header">
              <span class="vsm__section-title">{{ t('db.viewSettings.sectionViewType') }}</span>
            </div>

            <div class="vsm__vt-grid">
              <button
                v-for="vt in VIEW_TYPES"
                :key="vt.key"
                class="vsm__vt-btn"
                :class="{
                  'vsm__vt-btn--active':   localViewType === vt.key,
                  'vsm__vt-btn--planned':  vt.planned,
                }"
                :disabled="vt.planned"
                :title="vt.planned ? t('db.viewSettings.viewTypePlanned') : undefined"
                @click="setViewType(vt.key)"
              >
                <Icon :icon="vt.icon" width="20" height="20" />
                <span class="vsm__vt-label">{{ t(vt.labelKey) }}</span>
                <span v-if="vt.planned" class="vsm__vt-planned">{{ t('db.viewSettings.viewTypePlanned') }}</span>
              </button>
            </div>

            <!-- Calendar-specific: subtype + date property selector -->
            <template v-if="localViewType === 'calendar'">
              <!-- Subtype -->
              <div class="vsm__section-header vsm__section-header--spaced">
                <span class="vsm__section-title">{{ t('db.viewSettings.calendarSubtype') }}</span>
              </div>
              <div class="vsm__subtype-row">
                <button
                  class="vsm__subtype-btn"
                  :class="{ 'vsm__subtype-btn--active': localCalendarSubtype === 'standard' }"
                  @click="setCalendarSubtype('standard')"
                >
                  <Icon icon="mdi:calendar-month-outline" width="14" height="14" />
                  {{ t('db.viewSettings.calendarSubtypeStandard') }}
                </button>
                <button
                  class="vsm__subtype-btn"
                  :class="{ 'vsm__subtype-btn--active': localCalendarSubtype === 'agenda' }"
                  @click="setCalendarSubtype('agenda')"
                >
                  <Icon icon="mdi:format-list-bulleted-square" width="14" height="14" />
                  {{ t('db.viewSettings.calendarSubtypeAgenda') }}
                </button>
              </div>

              <!-- Date property -->
              <div class="vsm__section-header vsm__section-header--spaced">
                <span class="vsm__section-title">{{ t('db.viewSettings.calendarDateProperty') }}</span>
              </div>

              <div v-if="dateSchemas.length === 0" class="vsm__hint">
                {{ t('db.viewSettings.calendarNoDateSchemas') }}
              </div>
              <select
                v-else
                class="vsm__select"
                :value="localCalendarSchemaId"
                @change="setCalendarSchema(($event.target as HTMLSelectElement).value)"
              >
                <option value="">{{ t('db.viewSettings.calendarDatePropertyNone') }}</option>
                <option v-for="s in dateSchemas" :key="s.id" :value="s.id">{{ s.name }}</option>
              </select>

              <!-- Create date property if none exists -->
              <button
                v-if="dateSchemas.length === 0"
                class="vsm__create-date-btn"
                :disabled="isCreatingDateProp"
                @click="createDateProperty"
              >
                <Icon icon="mdi:plus" width="13" height="13" />
                {{ t('db.viewSettings.calendarCreateDateProp') }}
              </button>
            </template>
          </template>

          <!-- ── Section: Properties ──────────────────────────────────────── -->
          <template v-if="activeSection === 'properties'">
            <div class="vsm__section-header">
              <span class="vsm__section-title">{{ t('db.viewSettings.sectionProperties') }}</span>
              <div class="vsm__section-actions">
                <button class="vsm__link-btn" @click="showAll">
                  {{ t('db.viewSettings.showAll') }}
                </button>
                <button class="vsm__link-btn" @click="hideAllReadonly">
                  {{ t('db.viewSettings.hideSystem') }}
                </button>
                <button class="vsm__link-btn" @click="hideAll">
                  {{ t('db.viewSettings.hideAll') }}
                </button>
              </div>
            </div>

            <ul class="vsm__prop-list">
              <li
                v-for="col in allColumns"
                :key="col.key"
                class="vsm__prop-row"
                :class="{ 'vsm__prop-row--readonly': col.isReadonly }"
              >
                <Icon :icon="col.icon" width="14" height="14" class="vsm__prop-icon" />
                <span class="vsm__prop-name">{{ col.name }}</span>
                <span v-if="col.isReadonly" class="vsm__prop-badge">
                  {{ t('db.viewSettings.system') }}
                </span>
                <!-- Name column is always visible, no toggle -->
                <button
                  v-if="!col.isName"
                  class="vsm__toggle"
                  :class="{ 'vsm__toggle--on': isVisible(col.key) }"
                  :title="isVisible(col.key) ? t('db.viewSettings.hide') : t('db.viewSettings.show')"
                  @click="toggleColumn(col.key)"
                >
                  <Icon
                    :icon="isVisible(col.key) ? 'mdi:eye-outline' : 'mdi:eye-off-outline'"
                    width="15"
                    height="15"
                  />
                </button>
                <span v-else class="vsm__toggle-always">
                  <Icon icon="mdi:eye-outline" width="15" height="15" />
                </span>
              </li>
            </ul>
          </template>

          <!-- ── Section: Grouping (table view only) ─────────────────────── -->
          <template v-if="activeSection === 'grouping'">

            <!-- Group by property -->
            <div class="vsm__section-header">
              <span class="vsm__section-title">{{ t('db.viewSettings.groupByProperty') }}</span>
            </div>

            <select
              class="vsm__select"
              :value="localGroupBySchemaId"
              @change="setGroupBy(($event.target as HTMLSelectElement).value)"
            >
              <option value="">{{ t('db.viewSettings.groupByNone') }}</option>
              <option v-for="s in groupableSchemas" :key="s.id" :value="s.id">{{ s.name }}</option>
            </select>


          </template>

          <!-- ── Section: Headers & frozen columns (table view only) ─────── -->
          <template v-if="activeSection === 'headers'">

            <!-- Sticky column header -->
            <div class="vsm__section-header">
              <span class="vsm__section-title">{{ t('db.viewSettings.headersStickyTitle') }}</span>
            </div>

            <div class="vsm__check-row">
              <button
                class="vsm__toggle"
                :class="{ 'vsm__toggle--on': localStickyHeader }"
                role="checkbox"
                :aria-checked="localStickyHeader"
                :title="localStickyHeader ? t('db.viewSettings.hide') : t('db.viewSettings.show')"
                @click="setStickyHeader(!localStickyHeader)"
              >
                <Icon
                  :icon="localStickyHeader ? 'mdi:checkbox-marked-outline' : 'mdi:checkbox-blank-outline'"
                  width="18"
                  height="18"
                />
              </button>
              <div class="vsm__check-text">
                <span class="vsm__check-label">{{ t('db.viewSettings.stickyHeader') }}</span>
                <span class="vsm__hint">{{ t('db.viewSettings.stickyHeaderHint') }}</span>
              </div>
            </div>

            <!-- Frozen leftmost columns -->
            <div class="vsm__section-header vsm__section-header--spaced">
              <span class="vsm__section-title">{{ t('db.viewSettings.frozenColumns') }}</span>
            </div>

            <select
              class="vsm__select"
              :value="String(localFrozenColumns)"
              @change="setFrozenColumns(parseInt(($event.target as HTMLSelectElement).value, 10))"
            >
              <option v-for="n in frozenColumnOptions" :key="n" :value="String(n)">
                {{ n === 0 ? t('db.viewSettings.frozenColumnsNone') : String(n) }}
              </option>
            </select>
            <p class="vsm__hint">{{ t('db.viewSettings.frozenColumnsHint') }}</p>

          </template>

        </div>
      </div>

    </div>
  </div>
</template>

<style scoped>
/* ── Backdrop ────────────────────────────────────────────────────────────── */
.vsm-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 250;
}

.vsm {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  width: min(640px, 94vw);
  max-height: 84vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.25);
}

/* ── Header ──────────────────────────────────────────────────────────────── */
.vsm__header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 13px 16px;
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.vsm__header-title {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.875rem;
  font-weight: 600;
}

.vsm__header-view-name {
  font-weight: 400;
  color: var(--color-text-muted);
  font-size: 0.8rem;
}

.vsm__close {
  background: transparent;
  border: none;
  cursor: pointer;
  color: var(--color-text-muted);
  display: flex;
  align-items: center;
  padding: 3px;
  border-radius: 4px;
  transition: color 0.15s, background 0.15s;
}

.vsm__close:hover {
  color: var(--color-text);
  background: var(--color-hover);
}

/* ── Two-column body ─────────────────────────────────────────────────────── */
.vsm__body {
  display: flex;
  flex: 1;
  overflow: hidden;
}

/* ── Sidebar ─────────────────────────────────────────────────────────────── */
.vsm__sidebar {
  width: 180px;
  flex-shrink: 0;
  border-right: 1px solid var(--color-border);
  padding: 12px 8px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  overflow-y: auto;
}

.vsm__nav-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  border-radius: 5px;
  border: none;
  background: transparent;
  cursor: pointer;
  font-size: 0.82rem;
  color: var(--color-text-muted);
  text-align: left;
  transition: background 0.12s, color 0.12s;
}

.vsm__nav-item:hover {
  background: var(--color-hover);
  color: var(--color-text);
}

.vsm__nav-item--active {
  background: var(--color-accent-subtle);
  color: var(--color-accent);
  font-weight: 500;
}

/* ── Content pane ────────────────────────────────────────────────────────── */
.vsm__content {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* ── Section header ──────────────────────────────────────────────────────── */
.vsm__section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}

.vsm__section-title {
  font-size: 0.72rem;
  font-weight: 600;
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.vsm__section-actions {
  display: flex;
  gap: 8px;
}

.vsm__link-btn {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 0.75rem;
  color: var(--color-accent);
  padding: 0;
  transition: opacity 0.15s;
}

.vsm__link-btn:hover {
  opacity: 0.75;
}

/* ── Property list ───────────────────────────────────────────────────────── */
.vsm__prop-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.vsm__prop-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  border-radius: 5px;
  border: 1px solid var(--color-border);
  background: var(--color-bg);
  font-size: 0.85rem;
}

.vsm__prop-row--readonly {
  background: var(--color-surface);
}

.vsm__prop-icon {
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.vsm__prop-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.vsm__prop-badge {
  font-size: 0.65rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--color-text-muted);
  background: var(--color-hover);
  border-radius: 3px;
  padding: 1px 5px;
  flex-shrink: 0;
}

.vsm__toggle {
  background: transparent;
  border: none;
  cursor: pointer;
  padding: 3px;
  border-radius: 4px;
  color: var(--color-text-muted);
  display: flex;
  align-items: center;
  flex-shrink: 0;
  transition: color 0.15s, background 0.15s;
}

.vsm__toggle:hover {
  background: var(--color-hover);
  color: var(--color-text);
}

.vsm__toggle--on {
  color: var(--color-accent);
}

.vsm__toggle-always {
  padding: 3px;
  color: var(--color-accent);
  display: flex;
  align-items: center;
  flex-shrink: 0;
  opacity: 0.5;
}

/* ── View type grid ──────────────────────────────────────────────────────── */
.vsm__vt-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 6px;
}

.vsm__vt-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 5px;
  padding: 12px 8px 10px;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-bg);
  color: var(--color-text-muted);
  cursor: pointer;
  font-size: 0.75rem;
  transition: border-color 0.15s, color 0.15s, background 0.15s;
  position: relative;
  text-align: center;
}

.vsm__vt-btn:hover:not(:disabled) {
  border-color: var(--color-accent);
  color: var(--color-accent);
  background: var(--color-accent-subtle);
}

.vsm__vt-btn--active {
  border-color: var(--color-accent);
  color: var(--color-accent);
  background: var(--color-accent-subtle);
  font-weight: 600;
}

.vsm__vt-btn--planned {
  opacity: 0.5;
  cursor: not-allowed;
}

.vsm__vt-label {
  line-height: 1.2;
}

.vsm__vt-planned {
  position: absolute;
  top: 4px;
  right: 4px;
  font-size: 0.58rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--color-text-muted);
  background: var(--color-hover);
  border-radius: 2px;
  padding: 1px 3px;
}

/* ── Calendar config ─────────────────────────────────────────────────────── */
.vsm__section-header--spaced {
  margin-top: 16px;
}

.vsm__select {
  width: 100%;
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 5px;
  padding: 6px 8px;
  font-size: 0.82rem;
  color: var(--color-text);
  cursor: pointer;
}

.vsm__hint {
  font-size: 0.8rem;
  color: var(--color-text-muted);
  font-style: italic;
}

/* ── Subtype row ─────────────────────────────────────────────────────────── */
.vsm__subtype-row {
  display: flex;
  gap: 6px;
}

.vsm__subtype-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border: 1px solid var(--color-border);
  border-radius: 5px;
  background: var(--color-bg);
  color: var(--color-text-muted);
  font-size: 0.8rem;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s, background 0.15s;
  flex: 1;
  justify-content: center;
}

.vsm__subtype-btn:hover {
  border-color: var(--color-accent);
  color: var(--color-accent);
  background: var(--color-accent-subtle);
}

.vsm__subtype-btn--active {
  border-color: var(--color-accent);
  color: var(--color-accent);
  background: var(--color-accent-subtle);
  font-weight: 600;
}

/* ── Create date property button ─────────────────────────────────────────── */
.vsm__create-date-btn {
  display: flex;
  align-items: center;
  gap: 5px;
  background: var(--color-accent-subtle);
  border: 1px solid var(--color-accent);
  border-radius: 5px;
  color: var(--color-accent);
  font-size: 0.8rem;
  padding: 6px 10px;
  cursor: pointer;
  transition: opacity 0.15s;
  align-self: flex-start;
}

.vsm__create-date-btn:hover:not(:disabled) { opacity: 0.8; }
.vsm__create-date-btn:disabled { opacity: 0.5; cursor: not-allowed; }

/* ── Headers & frozen columns section ────────────────────────────────────── */
.vsm__check-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.vsm__check-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.vsm__check-label {
  font-size: 0.85rem;
  font-weight: 500;
  color: var(--color-text);
}

</style>

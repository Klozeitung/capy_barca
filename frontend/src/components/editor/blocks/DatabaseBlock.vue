<script setup lang="ts">
/**
 * DatabaseBlock
 *
 * Orchestrates database views, entries, schemas, and interactions.
 * Logic is delegated to focused composables; UI panels are extracted
 * into subcomponents. This file is intentionally the thin integration
 * layer that wires everything together.
 *
 * Composables
 * -----------
 * useViewManager   – views CRUD, tab drag-reorder, sort management
 * useFilterPanel   – filter-group state + mutations
 * useColumnResize  – column width pointer events
 * useExport        – CSV / XLSX / PDF / ICS export
 *
 * Subcomponents
 * -------------
 * FilterPanel  – filter UI (template + styles live in DatabaseBlock.css)
 * SortPanel    – sort UI
 */
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { useBlockStore } from '@/stores/blocks'
import { WS_BLOCK_EVENT, type BlockEventPayload } from '@/stores/ws'
import { useDrag } from '@/composables/useDrag'
import {
  useDatabaseStore,
  normalizeSelectOption,
  clampFrozenColumns,
  isStickyHeaderEnabled,
  type PropertySchema,
  type DatabaseEntry,
  type DatabaseView,
  type AggregationType,
  type ViewFilter,
  type FilterOperator,
  type EntryQueryFilterGroup,
  type KeyReference,
} from '@/stores/database'
import { isReadonlyPropertyType } from '@/stores/propertyTypes'

// ── Composables ───────────────────────────────────────────────────────────────
import { useViewManager, viewTypeIcon } from '@/composables/useViewManager'
import { useFilterPanel, getFormulaResultType } from '@/composables/useFilterPanel'
import { useColumnResize } from '@/composables/useColumnResize'
import { useExport } from '@/composables/useExport'

// ── Subcomponents ─────────────────────────────────────────────────────────────
import FilterPanel from './subcomponents/FilterPanel.vue'
import SortPanel from './subcomponents/SortPanel.vue'

// ── Other components ──────────────────────────────────────────────────────────
import AddSchemaPanel from './properties/AddSchemaPanel.vue'
import PropertySettingsModal from './properties/PropertySettingsModal.vue'
import KeyReferenceDeleteDialog from './properties/KeyReferenceDeleteDialog.vue'
import RelationCell from './properties/cells/RelationCell.vue'
import ViewSettingsModal from './properties/ViewSettingsModal.vue'
import CalendarView from './CalendarView.vue'
import AgendaView from './AgendaView.vue'
import IconPicker from '@/components/IconPicker.vue'
import { getSchemaIcon } from '@/stores/propertyTypes'
import CheckboxCell from './properties/cells/CheckboxCell.vue'
import SelectCell from './properties/cells/SelectCell.vue'
import MultiSelectCell from './properties/cells/MultiSelectCell.vue'
import DateCell from './properties/cells/DateCell.vue'
import LinkCell from './properties/cells/LinkCell.vue'
import FileCell from './properties/cells/FileCell.vue'
import ReadonlyCell from './properties/cells/ReadonlyCell.vue'
import TextCell from './properties/cells/TextCell.vue'
import RollupCell from './properties/cells/RollupCell.vue'
import FormulaCell from './properties/cells/FormulaCell.vue'
import SideView from '@/components/main/SideView.vue'
import AutomationsModal from './AutomationsModal.vue'
import DatabaseTemplateEditor from './DatabaseTemplateEditor.vue'
import TemplateManagerPanel from './subcomponents/TemplateManagerPanel.vue'

// ── Constants ─────────────────────────────────────────────────────────────────

const NAME_COL_KEY     = '__name__'
const PREF_VIEWS       = 'views'
const PREF_ACTIVE_VIEW = 'active_view'

// Window event emitted by BlockPropertySection when a property added from the
// side panel must be hidden in every view of this database. Kept as a
// plain string literal shared by both components to avoid a new shared module.
const DB_HIDE_SCHEMA_EVENT = 'capybarca:db-hide-schema-in-views'

const READONLY_SCHEMA_TYPES = new Set([
  'id', 'created_by', 'created_time', 'last_edited_by', 'last_edited_time',
  'parent_item', 'sub_item',
])

// ── Props ─────────────────────────────────────────────────────────────────────

const props = defineProps<{
  blockId: string
  /**
   * Set by BlockContentSection when rendered inline inside a content block.
   * Suppresses the large title / icon header.
   */
  inline?: boolean
}>()

// ── Dependencies ──────────────────────────────────────────────────────────────

const { t }       = useI18n()
const blockStore  = useBlockStore()
const dbStore     = useDatabaseStore()
const drag        = useDrag()

// ── Base state ────────────────────────────────────────────────────────────────

const block   = computed(() => blockStore.blocks[props.blockId])
const schemas = computed(() => dbStore.getSchemas(props.blockId))
const isLoading = ref(true)

// ── Server-side query state ───────────────────────────────────────────────────

const displayedEntries = ref<DatabaseEntry[]>([])
const totalEntries     = ref(0)

// ── Name search ───────────────────────────────────────────────────────────────

// A magnifier toggle in the toolbar expands into a text field that filters the
// rendered entries by name (case-insensitive substring match). It narrows only
// what is already loaded; the load-more bar still reflects the full server set.
const nameSearchActive = ref(false)
const nameSearchQuery  = ref('')
const searchInputEl    = ref<HTMLInputElement | null>(null)

// filteredAndSortedEntries is the single source the table, calendar, agenda,
// aggregations and export all render from. Applying the name filter here keeps
// every consumer consistent with what the user actually sees.
const filteredAndSortedEntries = computed<DatabaseEntry[]>(() => {
  const q = nameSearchQuery.value.trim().toLowerCase()
  if (!q) return displayedEntries.value
  return displayedEntries.value.filter((e) =>
    ((e.content?.title as string | undefined) ?? '').toLowerCase().includes(q),
  )
})

function toggleNameSearch(): void {
  nameSearchActive.value = !nameSearchActive.value
  if (nameSearchActive.value) {
    nextTick(() => searchInputEl.value?.focus())
  } else {
    nameSearchQuery.value = ''
  }
}

function clearNameSearch(): void {
  nameSearchQuery.value = ''
  nextTick(() => searchInputEl.value?.focus())
}

function onSearchBlur(): void {
  // Collapse the field automatically when left empty so it does not linger.
  if (!nameSearchQuery.value.trim()) nameSearchActive.value = false
}

// ── Display limit (load-more pagination) ─────────────────────────────────────

const INITIAL_DISPLAY_LIMIT = 100
const displayLimit = ref(INITIAL_DISPLAY_LIMIT)

/**
 * True when the server has more entries than the current page.
 * Drives the visibility of the load-more bar below the view.
 */
const hasMore = computed(() => totalEntries.value > displayedEntries.value.length)

// ── Limit-hint tooltip (teleported to body to avoid overflow clipping) ────────

interface TipState { text: string; subtext?: string; variant?: 'plain' | 'rich'; x: number; y: number; visible: boolean }

const tip = ref<TipState>({ text: '', x: 0, y: 0, visible: false })
let _tipTimer: ReturnType<typeof setTimeout> | null = null

function showTip(e: MouseEvent, text: string): void {
  if (!text) return
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
  if (_tipTimer) clearTimeout(_tipTimer)
  _tipTimer = setTimeout(() => {
    tip.value = { text, x: rect.left + rect.width / 2, y: rect.top, visible: true }
  }, 180)
}

/**
 * Column-header tooltip: shows the property name plus its free-text
 * description (config.description) as an italic second line when present.
 * Uses the shared db__tip element so a description can be styled distinctly
 * from the name — something a native title attribute cannot do.
 */
function showColumnTip(e: MouseEvent, schema: PropertySchema): void {
  const description = (schema.config?.description as string | undefined)?.trim()
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
  if (_tipTimer) clearTimeout(_tipTimer)
  _tipTimer = setTimeout(() => {
    tip.value = {
      text: schema.name,
      subtext: description || undefined,
      variant: 'rich',
      x: rect.left + rect.width / 2,
      y: rect.top,
      visible: true,
    }
  }, 180)
}

function hideTip(): void {
  if (_tipTimer) { clearTimeout(_tipTimer); _tipTimer = null }
  tip.value.visible = false
}

// ── Sub-item hierarchy ────────────────────────────────────────────────────────

/** The parent_item schema of this database, if seeded. */
const parentItemSchema = computed(() =>
  schemas.value.find(s => s.type === 'parent_item') ?? null,
)
/** The sub_item schema of this database, if seeded. */
const subItemSchema = computed(() =>
  schemas.value.find(s => s.type === 'sub_item') ?? null,
)

/**
 * Map from entry id → child ids, derived from parentMap (the inverse of
 * parent_item values). This is the authoritative source for tree rendering:
 * it is always consistent with subItemParentMap because it is computed from
 * the same parent_item values that the user writes directly.
 *
 * Previously this was built from sub_item values, which are a backend-managed
 * mirror. Using the mirror caused entries to disappear from the tree whenever
 * the sub_item value on the parent was stale or missing (e.g. immediately
 * after a parent_item write before the WS refresh arrived).
 *
 * Only populated when the sub_item schema exists (i.e. the pair is seeded).
 */
const subItemChildrenMap = computed((): Map<string, string[]> => {
  if (!subItemSchema.value) return new Map()
  const map = new Map<string, string[]>()
  for (const [childId, parentId] of subItemParentMap.value) {
    const existing = map.get(parentId)
    if (existing) {
      existing.push(childId)
    } else {
      map.set(parentId, [childId])
    }
  }
  return map
})

/**
 * Map from entry id → parent id, built from parent_item values.
 * Only populated when the parent_item schema exists.
 */
const subItemParentMap = computed((): Map<string, string> => {
  if (!parentItemSchema.value) return new Map()
  const pid = parentItemSchema.value.id
  const map = new Map<string, string>()
  for (const entry of displayedEntries.value) {
    const val = entry.values[pid]
    const parents = (val?.related_ids as string[] | undefined) ?? []
    if (parents.length > 0) map.set(entry.id, parents[0])
  }
  return map
})

// ── Fold state (localStorage-persisted, default: unfolded) ────────────────────

const foldedEntries = ref<Set<string>>(new Set())

function _foldKey(): string { return `db-fold-${props.blockId}` }

function loadFoldState(): void {
  try {
    const raw = localStorage.getItem(_foldKey())
    if (raw) foldedEntries.value = new Set(JSON.parse(raw))
  } catch { /* ignore */ }
}

function saveFoldState(): void {
  try {
    localStorage.setItem(_foldKey(), JSON.stringify([...foldedEntries.value]))
  } catch { /* ignore */ }
}

function toggleFold(entryId: string): void {
  const next = new Set(foldedEntries.value)
  if (next.has(entryId)) next.delete(entryId)
  else next.add(entryId)
  foldedEntries.value = next
  saveFoldState()
}

// ── Flat tree for table view (depth-first, respects fold state) ───────────────

interface FlatTreeEntry { entry: DatabaseEntry; depth: number }

/**
 * When no grouping is active and the sub-item schema pair is present,
 * renders the entry list as an indented tree (root entries first, then their
 * children recursively).  Falls back to flat rendering for grouped views or
 * when sub-items are not seeded.
 */
const flatTreeEntries = computed((): FlatTreeEntry[] => {
  const allEntries = filteredAndSortedEntries.value

  // Flat mode: grouping active, or sub-item pair not seeded yet.
  if (isGrouped.value || !subItemSchema.value) {
    return allEntries.map(e => ({ entry: e, depth: 0 }))
  }

  const entryById = new Map(allEntries.map(e => [e.id, e]))
  const parentMap  = subItemParentMap.value
  const childMap   = subItemChildrenMap.value

  // Root entries: those whose parent is absent from the current display set.
  const rootEntries = allEntries.filter(e => {
    const parentId = parentMap.get(e.id)
    return !parentId || !entryById.has(parentId)
  })

  const result: FlatTreeEntry[] = []
  const visited = new Set<string>() // cycle guard

  function walk(entry: DatabaseEntry, depth: number): void {
    if (visited.has(entry.id)) return
    visited.add(entry.id)
    result.push({ entry, depth })
    if (foldedEntries.value.has(entry.id)) return
    const childIds = childMap.get(entry.id) ?? []
    const children = childIds
      .map(id => entryById.get(id))
      .filter((e): e is DatabaseEntry => e !== undefined)
    // Preserve the server-side sort order among siblings.
    children.sort((a, b) => {
      const ai = allEntries.findIndex(e => e.id === a.id)
      const bi = allEntries.findIndex(e => e.id === b.id)
      return ai - bi
    })
    for (const child of children) walk(child, depth + 1)
  }

  for (const entry of rootEntries) walk(entry, 0)
  return result
})

// ── Composable: view manager ──────────────────────────────────────────────────
// queryFromActiveView is defined AFTER viewManager (it closes over activeView).
// The composable receives a stable function reference that calls the real
// implementation once it is defined, so onQueryNeeded works immediately.

// Stable reference for filter/sort/view-switch initiated queries.
// These reset displayLimit to INITIAL_DISPLAY_LIMIT before re-querying,
// whereas WS-driven refreshes and row operations keep the current limit.
let _resetQueryFn: () => Promise<void> = async () => {}

const vm = useViewManager({
  blockId:             props.blockId,
  schemas,
  NAME_COL_KEY,
  PREF_VIEWS,
  PREF_ACTIVE_VIEW,
  READONLY_SCHEMA_TYPES,
  onQueryNeeded:       () => _resetQueryFn(),
})

const {
  views, activeViewId, activeView,
  renamingViewId, viewNameDraft,
  tabDragId, tabDropId,
  viewTabMenuId, viewTabMenuPos,
  deleteConfirmViewId,
  viewSettingsViewId, viewSettingsView,
  showSortPanel, sortCount,
  saveViews, saveActiveViewId, buildDefaultView,
  addView,
  startRenameView, commitRenameView, cancelRenameView,
  onTabDragStart, onTabDragOver, onTabDragLeave, onTabDrop, onTabDragEnd,
  openViewTabMenu, promptDeleteView, cancelDeleteView, confirmDeleteView,
  duplicateView, onViewUpdated,
  openViewSettings, closeViewSettings,
  addSort, removeSort, onSortSchemaChange, onSortDirectionChange,
} = vm

// ── Query function (uses activeView from viewManager) ─────────────────────────

const isCalendarView = computed(() => activeView.value?.viewType === 'calendar')

/**
 * Calendar views (including the agenda subtype) must always receive the full
 * entry set — they render entries across a time axis and cannot work with a
 * paginated subset. All other views (table, kanban, …) use the display limit
 * for load-more pagination.
 */
const isUnlimitedView = computed(() =>
  activeView.value?.viewType === 'calendar',
)

async function queryFromActiveView(): Promise<void> {
  const view = activeView.value
  const filter_groups: EntryQueryFilterGroup[] = (view?.filterGroups ?? []).map((g) => ({
    conjunction: g.conjunction,
    filters: g.filters.map((f) => {
      const schema = schemas.value.find((s) => s.id === f.schemaId)
      return {
        schemaId:          f.schemaId,
        operator:          f.operator,
        value:             f.value,
        value2:            f.value2,
        dateMode:          f.dateMode,
        dateOffset:        f.dateOffset,
        formulaResultType: schema?.type === 'formula'
          ? getFormulaResultType(f.schemaId, displayedEntries.value)
          : undefined,
      }
    }),
  }))
  const sorts = (view?.sorts ?? []).map((s) => ({
    schemaId:  s.schemaId,
    direction: s.direction,
  }))
  const result = await dbStore.queryEntries(props.blockId, {
    filter_groups,
    sorts,
    limit: isUnlimitedView.value ? undefined : displayLimit.value,
  })
  displayedEntries.value = result.entries
  totalEntries.value     = result.total
}

// Wire the stable reference so viewManager's onQueryNeeded works.
async function resetAndQuery(): Promise<void> {
  displayLimit.value = INITIAL_DISPLAY_LIMIT
  await queryFromActiveView()
}

_resetQueryFn = resetAndQuery

/**
 * Extend the displayed page by the given number of rows and re-query.
 * Does not reset — continues from the current position.
 */
async function loadMore(increment: number): Promise<void> {
  displayLimit.value += increment
  await queryFromActiveView()
}

// ── Composable: filter panel ──────────────────────────────────────────────────

const fp = useFilterPanel({
  activeView,
  schemas,
  displayedEntries,
  nameColKey:      NAME_COL_KEY,
  saveViews,
  onQueryNeeded:   () => _resetQueryFn(),
})

const {
  showFilterPanel, filterCount,
  addGroup, removeGroup, onGroupConjunctionChange,
  addFilter, removeFilter,
  onFilterSchemaChange, onFilterOperatorChange, onFilterValueChange, onFilterValue2Change,
  onFilterDateModeChange, onFilterDateOffsetChange,
} = fp

// ── Composable: column resize ─────────────────────────────────────────────────

const resize = useColumnResize({ views, activeViewId, saveViews })

const { resizingKey, syncColWidthsFromView, startResize, colStyle, cleanup: resizeCleanup } = resize

// ── Composable: export ────────────────────────────────────────────────────────

const exp = useExport({
  orderedColumns:           computed(() => orderedColumns.value),
  filteredAndSortedEntries: filteredAndSortedEntries,
  block,
  activeView,
  schemas,
  isCalendarView,
  nameColLabel:  t('db.nameColumn'),
  t,
})

const { showExportMenu, exportCSV, exportExcel, exportPDF, exportICS } = exp

// ── Derived: column order ─────────────────────────────────────────────────────

interface OrderedColumn {
  key: string
  schema: PropertySchema | null
}

const orderedColumns = computed<OrderedColumn[]>(() => {
  const all    = schemas.value
  const order  = activeView.value?.colOrder ?? []
  const hidden = new Set(activeView.value?.hiddenColumns ?? [])
  // parent_item and sub_item are not rendered as columns — they drive the
  // tree structure (indent + fold) and appear only in BlockPropertySection.
  const TREE_SCHEMA_TYPES = new Set(['parent_item', 'sub_item'])
  const result: OrderedColumn[] = []
  const seen   = new Set<string>()

  for (const id of order) {
    if (id === NAME_COL_KEY) {
      result.push({ key: NAME_COL_KEY, schema: null })
      seen.add(NAME_COL_KEY)
    } else {
      if (hidden.has(id)) continue
      const s = all.find(sc => sc.id === id)
      if (s && !TREE_SCHEMA_TYPES.has(s.type)) { result.push({ key: id, schema: s }); seen.add(id) }
    }
  }

  for (const s of all) {
    if (!seen.has(s.id) && !hidden.has(s.id) && !TREE_SCHEMA_TYPES.has(s.type))
      result.push({ key: s.id, schema: s })
  }

  if (!seen.has(NAME_COL_KEY)) result.unshift({ key: NAME_COL_KEY, schema: null })

  return result
})

// ── Sticky header & frozen columns ────────────────────────────────────────────

/**
 * Sticky column header (vertical scroll) and frozen leftmost columns
 * (horizontal scroll) for the table view.
 *
 * Frozen left-offsets are measured from the rendered header-cell widths
 * (offsetWidth, scroll-independent) rather than offsetLeft, so a re-measure
 * while the table is scrolled horizontally cannot corrupt the values. The
 * handle column is pinned (left: 0) whenever at least one column is frozen.
 */
const theadRowEl = ref<HTMLElement | null>(null)

const stickyHeaderEnabled = computed<boolean>(() => isStickyHeaderEnabled(activeView.value))

const frozenColumnCount = computed<number>(() => clampFrozenColumns(activeView.value?.frozenColumns))

/** Keys of the leftmost columns to freeze, in render order. */
const frozenColumnKeys = computed<Set<string>>(
  () => new Set(orderedColumns.value.slice(0, frozenColumnCount.value).map(c => c.key)),
)

/** Whether the drag-handle column is pinned (true when any column is frozen). */
const handleFrozen = computed<boolean>(() => frozenColumnCount.value > 0)

/** Measured left offset (px) per frozen column key. */
const frozenOffsets = ref<Record<string, number>>({})

function measureFrozenOffsets(): void {
  const count = frozenColumnCount.value
  if (count === 0) {
    if (Object.keys(frozenOffsets.value).length > 0) frozenOffsets.value = {}
    return
  }
  const row = theadRowEl.value
  if (!row) return
  const cells = Array.from(row.children) as HTMLElement[]
  // cells[0] = handle, cells[1..] = orderedColumns, last = add-column placeholder.
  const next: Record<string, number> = {}
  let acc = cells[0] ? cells[0].offsetWidth : 0
  const cols = orderedColumns.value
  for (let i = 0; i < count && i < cols.length; i++) {
    next[cols[i].key] = acc
    const cell = cells[i + 1]
    acc += cell ? cell.offsetWidth : 0
  }
  frozenOffsets.value = next
}

/** Inline style (left offset) for a frozen data/header column cell. */
function frozenColStyle(key: string): Record<string, string> {
  if (!frozenColumnKeys.value.has(key)) return {}
  const left = frozenOffsets.value[key]
  return left == null ? {} : { left: `${left}px` }
}

/** True when a column is frozen AND its offset has been measured. */
function isFrozenCol(key: string): boolean {
  return frozenColumnKeys.value.has(key) && frozenOffsets.value[key] != null
}

/** Inline style for the pinned drag-handle column. */
const handleFrozenStyle = computed<Record<string, string>>(
  () => (handleFrozen.value ? { left: '0px' } : ({} as Record<string, string>)),
)

let _frozenRO: ResizeObserver | null = null

function _attachFrozenObserver(): void {
  _frozenRO?.disconnect()
  _frozenRO = null
  const el = theadRowEl.value
  if (el && typeof ResizeObserver !== 'undefined') {
    _frozenRO = new ResizeObserver(() => measureFrozenOffsets())
    _frozenRO.observe(el)
  }
}

// Re-measure / re-observe whenever the rendered table structure can change.
watch(
  () => [
    activeViewId.value,
    activeView.value?.viewType,
    frozenColumnCount.value,
    orderedColumns.value.length,
  ] as const,
  () => nextTick(() => { _attachFrozenObserver(); measureFrozenOffsets() }),
)

// ── Panel / menu visibility ───────────────────────────────────────────────────

const showAutomationsModal = ref(false)
const showTemplatesModal = ref(false)
/** ID of the entry_template block currently open in the editor, or null. */
const editingTemplateId = ref<string | null>(null)

function closeAllPanels() {
  showFilterPanel.value    = false
  showSortPanel.value      = false
  showExportMenu.value     = false
  aggPickerKey.value       = null
  viewTabMenuId.value      = null
  clearActiveCell()
  closeRowContextMenu()
}

// ── Bootstrap ─────────────────────────────────────────────────────────────────

function _onDbEntriesUpdated(e: Event): void {
  const payload = (e as CustomEvent<BlockEventPayload>).detail
  if (
    payload.event_type === 'database_entries_updated' &&
    (payload.after as { database_id?: string } | null)?.database_id === props.blockId
  ) {
    queryFromActiveView()
  }
}

async function _handleRemoteSchemaUpdate(): Promise<void> {
  const knownIds = new Set(schemas.value.map((s) => s.id))
  await dbStore.fetchSchemas(props.blockId)
  const newSchemas = schemas.value.filter((s) => !knownIds.has(s.id))
  if (newSchemas.length === 0) return

  let dirty = false
  for (const view of views.value) {
    if (!view.hiddenColumns) view.hiddenColumns = []
    for (const s of newSchemas) {
      if (!view.hiddenColumns.includes(s.id)) {
        view.hiddenColumns.push(s.id)
        dirty = true
      }
    }
  }
  if (dirty) await saveViews()
}

function _onDbSchemaUpdated(e: Event): void {
  const payload = (e as CustomEvent<BlockEventPayload>).detail
  if (
    payload.event_type === 'database_schema_updated' &&
    (payload.after as { database_id?: string } | null)?.database_id === props.blockId
  ) {
    _handleRemoteSchemaUpdate()
  }
}

/**
 * A property added from the property section (side panel / full-page
 * entry view) must be hidden in every view of this database. That schema is
 * created on the same client, so it is already in the store by the time the
 * standard "remote schema" path runs — that path only reacts to
 * previously-unknown schemas and would do nothing here. BlockPropertySection
 * therefore emits this dedicated event so a live DatabaseBlock can sync its
 * in-memory views immediately. Persistence is owned by the dispatcher.
 */
function _onHideSchemaInViews(e: Event): void {
  const detail = (e as CustomEvent<{ databaseId?: string; schemaId?: string }>).detail
  if (!detail || detail.databaseId !== props.blockId || !detail.schemaId) return
  const schemaId = detail.schemaId
  for (const view of views.value) {
    if (!view.hiddenColumns) view.hiddenColumns = []
    if (!view.hiddenColumns.includes(schemaId)) {
      view.hiddenColumns.push(schemaId)
    }
  }
}

onMounted(async () => {
  await Promise.all([
    blockStore.fetchBlock(props.blockId),
    blockStore.fetchPreferences(props.blockId),
    dbStore.fetchSchemas(props.blockId),
  ])

  await dbStore.seedReadonlySchemas(props.blockId)

  const rawViews    = blockStore.getPreference(props.blockId, PREF_VIEWS, null) as DatabaseView[] | null
  const rawActiveId = blockStore.getPreference(props.blockId, PREF_ACTIVE_VIEW, null) as string | null

  if (rawViews && Array.isArray(rawViews) && rawViews.length > 0) {
    // Migrate legacy flat filters and missing viewType fields.
    let migrated = false
    for (const v of rawViews) {
      if (!v.viewType) { v.viewType = 'table'; migrated = true }
      if (!v.filterGroups) { v.filterGroups = []; migrated = true }
      if (v.filters && v.filters.length > 0) {
        v.filterGroups.push({
          id: crypto.randomUUID(),
          conjunction: 'and',
          filters: v.filters,
        })
        v.filters = []
        migrated = true
      }
    }
    views.value = rawViews
    activeViewId.value =
      rawActiveId && rawViews.some((v) => v.id === rawActiveId)
        ? rawActiveId
        : rawViews[0].id
    if (migrated) await saveViews()
  } else {
    const legacyWidths = blockStore.getPreference(props.blockId, 'col_widths', {}) as Record<string, number>
    const defaultView  = buildDefaultView(legacyWidths)
    views.value        = [defaultView]
    activeViewId.value = defaultView.id
    await saveViews()
    await saveActiveViewId()
  }

  syncColWidthsFromView(activeView.value)
  loadFoldState()
  await queryFromActiveView()
  isLoading.value = false
  document.addEventListener('click', closeAllPanels)
  window.addEventListener(WS_BLOCK_EVENT, _onDbEntriesUpdated)
  window.addEventListener(WS_BLOCK_EVENT, _onDbSchemaUpdated)
  window.addEventListener(DB_HIDE_SCHEMA_EVENT, _onHideSchemaInViews)
  window.addEventListener('resize', measureFrozenOffsets)
  nextTick(() => { _attachFrozenObserver(); measureFrozenOffsets() })
})

onUnmounted(() => {
  document.removeEventListener('click', closeAllPanels)
  window.removeEventListener(WS_BLOCK_EVENT, _onDbEntriesUpdated)
  window.removeEventListener(WS_BLOCK_EVENT, _onDbSchemaUpdated)
  window.removeEventListener(DB_HIDE_SCHEMA_EVENT, _onHideSchemaInViews)
  window.removeEventListener('resize', measureFrozenOffsets)
  _frozenRO?.disconnect()
  _frozenRO = null
  resizeCleanup()
  if (_tipTimer) clearTimeout(_tipTimer)
})

// ── View switching (orchestrates multiple composables) ────────────────────────

async function switchView(viewId: string): Promise<void> {
  if (activeViewId.value === viewId) return
  activeViewId.value = viewId
  activeCell.value   = null
  displayLimit.value = INITIAL_DISPLAY_LIMIT
  syncColWidthsFromView(activeView.value)
  await saveActiveViewId()
  await queryFromActiveView()
}

// ── Add schema ────────────────────────────────────────────────────────────────

const showAddSchema = ref(false)

async function onAddSchemaPanelClose(newSchemaId?: string): Promise<void> {
  showAddSchema.value = false
  if (!newSchemaId) return
  let changed = false
  for (const view of views.value) {
    if (view.id === activeViewId.value) continue
    if (!view.hiddenColumns.includes(newSchemaId)) {
      view.hiddenColumns = [...view.hiddenColumns, newSchemaId]
      changed = true
    }
  }
  if (changed) await saveViews()
}

// ── Property settings modal ───────────────────────────────────────────────────

const settingsSchema = ref<PropertySchema | null>(null)

function openSettings(schema: PropertySchema)  { settingsSchema.value = schema }
function closeSettings()                        { settingsSchema.value = null }

// ── Delete schema ─────────────────────────────────────────────────────────────

const deletingSchemaId = ref<string | null>(null)

/**
 * Relations keyed on the column about to be deleted.
 *
 * Empty for almost every column, in which case the inline check / cancel pair
 * behaves exactly as before. When it is not empty the inline confirmation is
 * skipped in favour of a dialog that names the relations losing their sort
 * order — information two small buttons cannot carry.
 */
const keyReferences = ref<KeyReference[]>([])
const keyDeleteSchemaId = ref<string | null>(null)

const keyDeleteSchemaName = computed(
  () => dbStore.getSchemas(props.blockId).find(s => s.id === keyDeleteSchemaId.value)?.name ?? '',
)

async function promptDeleteSchema(schemaId: string): Promise<void> {
  // Pre-flight before arming: a deliberate click, not something a render does.
  const references = await dbStore.listKeyReferences(props.blockId, schemaId)
  if (references.length > 0) {
    keyReferences.value = references
    keyDeleteSchemaId.value = schemaId
    return
  }
  deletingSchemaId.value = schemaId
}

function cancelDeleteSchema()                  { deletingSchemaId.value = null }

async function confirmDeleteSchema(schemaId: string): Promise<void> {
  deletingSchemaId.value = null
  await dbStore.deleteSchema(props.blockId, schemaId)
  await queryFromActiveView()
}

function cancelKeyDelete(): void {
  keyDeleteSchemaId.value = null
  keyReferences.value = []
}

async function confirmKeyDelete(): Promise<void> {
  const schemaId = keyDeleteSchemaId.value
  cancelKeyDelete()
  if (schemaId) await confirmDeleteSchema(schemaId)
}

// ── Column drag-and-drop reorder ──────────────────────────────────────────────

const colDragKey = ref<string | null>(null)
const colDropKey = ref<string | null>(null)

function onColDragStart(e: DragEvent, key: string) {
  if (resizingKey.value !== null) { e.preventDefault(); return }
  colDragKey.value = key
  if (e.dataTransfer) { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', key) }
}

function onColDragOver(e: DragEvent, key: string) {
  if (colDragKey.value === null || colDragKey.value === key) return
  e.preventDefault()
  colDropKey.value = key
}

function onColDragLeave(key: string) {
  if (colDropKey.value === key) colDropKey.value = null
}

function onColDrop(e: DragEvent, targetKey: string) {
  e.preventDefault()
  colDropKey.value = null
  const sourceKey = colDragKey.value
  colDragKey.value = null
  if (!sourceKey || sourceKey === targetKey) return
  const view = activeView.value
  if (!view) return
  const order   = orderedColumns.value.map(c => c.key)
  const fromIdx = order.indexOf(sourceKey)
  const toIdx   = order.indexOf(targetKey)
  if (fromIdx === -1 || toIdx === -1) return
  order.splice(fromIdx, 1)
  order.splice(toIdx, 0, sourceKey)
  view.colOrder = order
  saveViews()
}

function onColDragEnd() { colDragKey.value = null; colDropKey.value = null }

// ── Row drag & drop ───────────────────────────────────────────────────────────
//
// Three-zone interaction per row:
//   top 25%    → above  (sibling reorder: insert before target)
//   middle 50% → onto   (relation: normal drop sets parent_item;
//                         Ctrl+drop moves block as actual child)
//   bottom 25% → below  (sibling reorder: insert after target)

interface RowDropState { above: boolean; below: boolean; onto: boolean }

const rowDropStates = ref<Record<string, RowDropState>>({})

function getRowDropState(id: string): RowDropState {
  return rowDropStates.value[id] ?? { above: false, below: false, onto: false }
}

function clearRowDropState(id: string): void {
  delete rowDropStates.value[id]
}

let _rowDragInProgress = false

function onRowDragStart(e: DragEvent, entry: DatabaseEntry): void {
  // Restrict drag initiation to the dedicated row handle element.
  // draggable="true" now lives on the .db__row-handle span, not on the <tr>,
  // so legitimate drags already arrive with e.target inside the handle.
  // e.preventDefault() on non-handle events cancels the browser drag gesture
  // and prevents it from interfering with cell click / cursor placement.
  const handle = (e.target as HTMLElement).closest?.('.db__row-handle')
  if (!handle) {
    e.preventDefault()
    return
  }
  e.stopPropagation()
  e.dataTransfer!.effectAllowed = 'move'
  _rowDragInProgress = true
  drag.startDrag(entry.id, props.blockId, 'page')
}

function onRowDragEnd(entry: DatabaseEntry): void {
  drag.endDrag()
  clearRowDropState(entry.id)
  setTimeout(() => { _rowDragInProgress = false }, 0)
}

function onRowDragOver(e: DragEvent, entry: DatabaseEntry): void {
  const { blockId, dragMode } = drag.getDragging()
  if (!blockId || dragMode === 'column') return
  e.preventDefault()
  e.stopPropagation()
  e.dataTransfer!.dropEffect = 'move'
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
  const rel  = (e.clientY - rect.top) / rect.height
  rowDropStates.value[entry.id] =
    rel < 0.25 ? { above: true,  below: false, onto: false }
    : rel > 0.75 ? { above: false, below: true,  onto: false }
    : { above: false, below: false, onto: true }
}

async function onRowDrop(e: DragEvent, entry: DatabaseEntry): Promise<void> {
  const { blockId: draggedId, dragMode } = drag.getDragging()
  if (!draggedId || dragMode === 'column') return
  e.preventDefault()
  e.stopPropagation()
  const state = getRowDropState(entry.id)
  clearRowDropState(entry.id)

  if (state.onto && draggedId !== entry.id) {
    // Middle-zone drop: set the dragged entry as a subitem of the target entry
    // via the parent_item relation. No block-hierarchy reparenting occurs.
    if (parentItemSchema.value) {
      drag.endDrag()
      await dbStore.upsertValue(
        props.blockId, draggedId, parentItemSchema.value.id,
        { related_ids: [entry.id] },
      )
    } else {
      // Sub-item pair not seeded yet — fall back to plain position reorder.
      const list = flatTreeEntries.value.map(r => r.entry)
      const idx  = list.findIndex(r => r.id === entry.id)
      const after = idx < list.length - 1 ? list[idx + 1].position : null
      await drag.dropBetween(props.blockId, entry.position, after)
    }
    await queryFromActiveView()
    return
  }

  // Above / below → position-based sibling reorder (existing behaviour).
  const list = flatTreeEntries.value.map(r => r.entry)
  const idx  = list.findIndex(r => r.id === entry.id)
  if (state.above) {
    const before = idx > 0 ? list[idx - 1].position : null
    await drag.dropBetween(props.blockId, before, entry.position)
  } else {
    const after = idx < list.length - 1 ? list[idx + 1].position : null
    await drag.dropBetween(props.blockId, entry.position, after)
  }
  await queryFromActiveView()
}

// ── Row context menu ──────────────────────────────────────────────────────────

interface RowContextMenu { visible: boolean; x: number; y: number; entryId: string | null }

const rowContextMenu   = ref<RowContextMenu>({ visible: false, x: 0, y: 0, entryId: null })
const rowContextMenuEl = ref<HTMLElement | null>(null)

function openRowContextMenu(e: MouseEvent, entry: DatabaseEntry): void {
  if (_rowDragInProgress) return
  e.stopPropagation()
  rowContextMenu.value = { visible: true, x: e.clientX, y: e.clientY, entryId: entry.id }
  nextTick(() => {
    if (!rowContextMenuEl.value) return
    const { offsetWidth: w, offsetHeight: h } = rowContextMenuEl.value
    const vw = window.innerWidth; const vh = window.innerHeight
    if (rowContextMenu.value.x + w > vw) rowContextMenu.value.x = vw - w - 8
    if (rowContextMenu.value.y + h > vh) rowContextMenu.value.y = vh - h - 8
  })
}

function closeRowContextMenu(): void {
  rowContextMenu.value = { visible: false, x: 0, y: 0, entryId: null }
}

async function contextMenuDuplicateEntry(): Promise<void> {
  const id = rowContextMenu.value.entryId
  closeRowContextMenu()
  if (!id) return
  await dbStore.duplicateEntry(props.blockId, id)
  await queryFromActiveView()
}

async function contextMenuDeleteEntry(): Promise<void> {
  const id = rowContextMenu.value.entryId
  closeRowContextMenu()
  if (!id) return
  await blockStore.deleteBlock(id, props.blockId)
  await queryFromActiveView()
}

// ── Database title ────────────────────────────────────────────────────────────

const titleDraft = ref('')

function onTitleMount() {
  titleDraft.value = (block.value?.content?.title as string | undefined) ?? ''
}

async function saveTitle() {
  const trimmed = titleDraft.value.trim()
  const current = (block.value?.content?.title as string | undefined) ?? ''
  if (trimmed === current) return
  await blockStore.updateBlock(props.blockId, {
    content: { ...(block.value?.content ?? {}), title: trimmed },
  })
}

// ── Icon picker ───────────────────────────────────────────────────────────────

const showIconPicker = ref(false)

async function onIconUpdate(newIcon: string | null): Promise<void> {
  showIconPicker.value = false
  if (!newIcon || newIcon === block.value?.icon) return
  await blockStore.updateAppearance(props.blockId, { icon: newIcon })
}

// ── Schema icon picker ────────────────────────────────────────────────────────

const schemaIconPickerKey  = ref<string | null>(null)
const schemaIconPickerRect = ref<DOMRect | null>(null)

function openSchemaIconPicker(schema: PropertySchema, event: MouseEvent): void {
  schemaIconPickerKey.value  = schema.id
  schemaIconPickerRect.value = (event.currentTarget as HTMLElement).getBoundingClientRect()
}

async function onSchemaIconUpdate(schema: PropertySchema, newIcon: string | null): Promise<void> {
  schemaIconPickerKey.value  = null
  schemaIconPickerRect.value = null
  await dbStore.updateSchema(props.blockId, schema.id, {
    config: { ...(schema.config ?? {}), icon: newIcon ?? undefined },
  })
}

// ── Entry icon picker ─────────────────────────────────────────────────────────

/** ID of the entry whose icon picker is currently open, or null. */
const entryIconPickerEntryId = ref<string | null>(null)
const entryIconPickerRect    = ref<DOMRect | null>(null)

function openEntryIconPicker(entry: DatabaseEntry, event: MouseEvent): void {
  event.stopPropagation()
  entryIconPickerEntryId.value = entry.id
  entryIconPickerRect.value    = (event.currentTarget as HTMLElement).getBoundingClientRect()
}

async function onEntryIconUpdate(newIcon: string | null): Promise<void> {
  const id = entryIconPickerEntryId.value
  entryIconPickerEntryId.value = null
  entryIconPickerRect.value    = null
  if (!id) return
  await blockStore.updateAppearance(id, { icon: newIcon ?? undefined })
  // Optimistically sync the icon into displayedEntries so the cell updates
  // immediately without waiting for the next full queryFromActiveView().
  const row = displayedEntries.value.find(e => e.id === id)
  if (row) row.icon = newIcon ?? null
}

/**
 * Resolve the display icon for a row entry.
 * Prefer blockStore.blocks so that a freshly-set icon appears instantly even
 * before the next queryFromActiveView() refreshes displayedEntries.
 */
function entryIcon(entry: DatabaseEntry): string {
  return blockStore.blocks[entry.id]?.icon ?? entry.icon ?? 'mdi:file-outline'
}

// ── Name cell editing ─────────────────────────────────────────────────────────

const editingNameId = ref<string | null>(null)
const nameDraft     = ref('')

function startNameEdit(entry: DatabaseEntry) {
  editingNameId.value = entry.id
  nameDraft.value     = (entry.content?.title as string | undefined) ?? ''
}

async function saveNameEdit(entry: DatabaseEntry) {
  if (editingNameId.value !== entry.id) return
  editingNameId.value = null
  const trimmed = nameDraft.value.trim()
  const current = (entry.content?.title as string | undefined) ?? ''
  if (trimmed === current) return
  await blockStore.updateBlock(entry.id, {
    content: { ...(entry.content ?? {}), title: trimmed },
  })
  await queryFromActiveView()
}

// ── Side view ─────────────────────────────────────────────────────────────────

const sideViewEntryId = ref<string | null>(null)

function openEntry(entry: DatabaseEntry) { sideViewEntryId.value = entry.id }
function closeSideView(): void           { sideViewEntryId.value = null }

async function onSideViewRefresh(): Promise<void> {
  await queryFromActiveView()
}

// ── Active cell ───────────────────────────────────────────────────────────────

interface ActiveCell { entryId: string; schemaId: string }

const activeCell = ref<ActiveCell | null>(null)

function isActiveCell(entryId: string, schemaId: string): boolean {
  return activeCell.value?.entryId === entryId && activeCell.value?.schemaId === schemaId
}

function setActiveCell(entryId: string, schemaId: string): void {
  if (isReadonlyPropertyType(schemas.value.find(s => s.id === schemaId)?.type ?? '')) return
  activeCell.value = { entryId, schemaId }
}

function clearActiveCell(): void { activeCell.value = null }

function onCellClick(entry: DatabaseEntry, schema: PropertySchema): void {
  if (isReadonlyPropertyType(schema.type)) return
  if (schema.type === 'checkbox' || schema.type === 'relation' || schema.type === 'file') return
  if (schema.type === 'select' && (schema.config?.mode ?? 'single') === 'multiple') return
  setActiveCell(entry.id, schema.id)
}

// ── Relation change ───────────────────────────────────────────────────────────

async function handleRelationChange(
  entry: DatabaseEntry,
  schema: PropertySchema,
  value: Record<string, unknown> | null,
): Promise<void> {
  await dbStore.upsertValue(props.blockId, entry.id, schema.id, value)
  if (schema.type === 'parent_item') {
    // sub_item mirrors were updated server-side; refresh the full list so the
    // tree rebuilds with the correct parent/child relationships.
    await queryFromActiveView()
    return
  }
  if (schema.type === 'relation' && schema.config?.direction === 'bilateral') {
    const targetId  = schema.config.target_database_id as string | undefined
    const refetches: Promise<unknown>[] = [
      dbStore.fetchSchemas(props.blockId),
      queryFromActiveView(),
    ]
    if (targetId && targetId !== props.blockId) {
      refetches.push(dbStore.fetchSchemas(targetId))
      // The bilateral mirror value was written server-side on the target
      // entry. Without re-fetching the target database's entries, its cached
      // values stay stale and any SideView opened on a linked target entry
      // shows an empty synced relation until a full page reload.
      refetches.push(dbStore.fetchEntries(targetId))
    }
    await Promise.all(refetches)
  }
}

// ── Add row ───────────────────────────────────────────────────────────────────

const isAddingRow = ref(false)

function deriveInitialValuesFromFilters(): {
  values: Record<string, Record<string, unknown>>
  name: string | null
} {
  const view = activeView.value
  if (!view || view.filterGroups.length === 0) return { values: {}, name: null }

  const SKIP_TYPES = new Set([
    'id', 'created_by', 'created_time', 'last_edited_by', 'last_edited_time',
    'formula', 'rollup', 'file',
  ])

  function todayISO(): string { return new Date().toISOString().slice(0, 10) }
  function shiftDate(iso: string, days: number): string {
    const d = new Date(iso + 'T00:00:00')
    d.setDate(d.getDate() + days)
    return d.toISOString().slice(0, 10)
  }
  function resolveDateRef(filter: ViewFilter): string | null {
    const mode  = filter.dateMode ?? 'exact'
    const today = todayISO()
    if (mode === 'today')    return today
    if (mode === 'relative') return shiftDate(today, filter.dateOffset ?? 0)
    return filter.value || null
  }

  function deriveFilterValue(filter: ViewFilter): Record<string, unknown> | null {
    const { schemaId, operator, value } = filter
    if (schemaId === NAME_COL_KEY) return null
    const schema = schemas.value.find((s) => s.id === schemaId)
    if (!schema || SKIP_TYPES.has(schema.type)) return null
    const today = todayISO()
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
      default: return null
    }
  }

  function deriveNameValue(filter: ViewFilter): string | null {
    if (!['eq', 'contains', 'starts_with'].includes(filter.operator)) return null
    return filter.value || null
  }

  const result: Record<string, Record<string, unknown>> = {}
  let derivedName: string | null = null

  for (const group of view.filterGroups) {
    if (group.conjunction === 'and') {
      for (const filter of group.filters) {
        if (filter.schemaId === NAME_COL_KEY) {
          if (derivedName === null) derivedName = deriveNameValue(filter)
          continue
        }
        if (result[filter.schemaId] !== undefined) continue
        const v = deriveFilterValue(filter)
        if (v !== null) result[filter.schemaId] = v
      }
    } else {
      let picked = false
      for (const filter of group.filters) {
        if (picked) break
        if (filter.schemaId === NAME_COL_KEY) {
          if (derivedName === null) {
            const n = deriveNameValue(filter)
            if (n !== null) { derivedName = n; picked = true }
          }
          continue
        }
        if (result[filter.schemaId] !== undefined) { picked = true; continue }
        const v = deriveFilterValue(filter)
        if (v !== null) { result[filter.schemaId] = v; picked = true }
      }
    }
  }

  return { values: result, name: derivedName }
}

async function addRow() {
  if (isAddingRow.value) return
  isAddingRow.value = true
  try {
    const { values, name } = deriveInitialValuesFromFilters()
    const created = await dbStore.createEntry(props.blockId)
    if (Object.keys(values).length > 0) {
      await Promise.all(
        Object.entries(values).map(([schemaId, value]) =>
          dbStore.upsertValue(props.blockId, created.id, schemaId, value),
        ),
      )
    }
    await queryFromActiveView()
    editingNameId.value = created.id
    nameDraft.value     = name ?? ''
    await nextTick()
    const input = document.querySelector<HTMLElement>(`[data-entry-id="${created.id}"] .db__cell-input`)
    input?.scrollIntoView({ block: 'nearest' })
    input?.focus()
  } finally {
    isAddingRow.value = false
  }
}

async function addRowOnDate(dateStr: string): Promise<void> {
  const view = activeView.value
  if (!view?.calendarDateSchemaId) return
  if (isAddingRow.value) return
  isAddingRow.value = true
  try {
    const { values } = deriveInitialValuesFromFilters()
    const created    = await dbStore.createEntry(props.blockId)
    const merged: Record<string, Record<string, unknown>> = { ...values }
    merged[view.calendarDateSchemaId] = { start: dateStr, end: dateStr }
    await Promise.all(
      Object.entries(merged).map(([schemaId, value]) =>
        dbStore.upsertValue(props.blockId, created.id, schemaId, value),
      ),
    )
    await queryFromActiveView()
  } finally {
    isAddingRow.value = false
  }
}

// ── Group-by feature ──────────────────────────────────────────────────────────

const groupBySchema = computed<PropertySchema | null>(() => {
  const id = activeView.value?.groupBySchemaId
  if (!id) return null
  return schemas.value.find(s => s.id === id) ?? null
})

const isGrouped = computed<boolean>(() =>
  (activeView.value?.viewType === 'table' || !activeView.value) &&
  groupBySchema.value !== null,
)

const aggPickerKey = ref<string | null>(null)

function setAggregation(colKey: string, fn: AggregationType): void {
  const view = activeView.value
  if (!view) return
  view.columnAggregations = { ...(view.columnAggregations ?? {}), [colKey]: fn }
  saveViews()
  aggPickerKey.value = null
}

function aggOptionsForCol(schema: PropertySchema | null): AggregationType[] {
  const isNum = schema?.type === 'number' || schema?.type === 'formula'
  const base: AggregationType[] = ['none', 'count']
  if (isNum) base.push('sum', 'avg', 'min', 'max')
  return base
}

interface TableGroup {
  key: string
  label: string
  entries: DatabaseEntry[]
  groupValue: Record<string, unknown> | null
}

function getEntryGroupKey(entry: DatabaseEntry, schema: PropertySchema): string {
  const val = entry.values[schema.id]
  if (!val) return ''
  switch (schema.type) {
    case 'select': {
      const mode = (schema.config?.mode as string | undefined) ?? 'single'
      if (mode === 'multiple') {
        const opts = (val.options as string[] | undefined) ?? []
        return [...opts].sort().join('\x00')
      }
      return (val.option as string | undefined) ?? ''
    }
    case 'text':    return (val.text   as string | undefined) ?? ''
    case 'number':  return val.number != null ? String(val.number) : ''
    case 'checkbox': return String(val.checked ?? false)
    case 'email':
    case 'phone':
    case 'url':     return (val.value as string | undefined) ?? ''
    default:        return ''
  }
}

function groupKeyToValue(key: string, schema: PropertySchema): Record<string, unknown> | null {
  if (!key) return null
  switch (schema.type) {
    case 'select': {
      const mode = (schema.config?.mode as string | undefined) ?? 'single'
      if (mode === 'multiple') return { options: key.split('\x00') }
      return { option: key }
    }
    case 'text':     return { text: key }
    case 'number':   return { number: parseFloat(key) }
    case 'checkbox': return { checked: key === 'true' }
    case 'email':
    case 'phone':
    case 'url':      return { value: key }
    default:         return null
  }
}

const tableGroups = computed<TableGroup[]>(() => {
  const schema = groupBySchema.value
  if (!schema) return []

  const groupMap = new Map<string, TableGroup>()

  for (const entry of filteredAndSortedEntries.value) {
    const key = getEntryGroupKey(entry, schema)
    if (!groupMap.has(key)) {
      const label = key !== ''
        ? (schema.type === 'checkbox'
            ? (key === 'true' ? t('db.filter.checkboxTrue') : t('db.filter.checkboxFalse'))
            : key)
        : t('db.groupBy.ungrouped')
      groupMap.set(key, { key, label, entries: [], groupValue: groupKeyToValue(key, schema) })
    }
    groupMap.get(key)!.entries.push(entry)
  }

  let orderedKeys: string[]
  if (schema.type === 'select' && (schema.config?.mode as string | undefined) !== 'multiple') {
    const rawOpts    = (schema.config?.options as unknown[] | undefined) ?? []
    const optionLabels = rawOpts.map(o => normalizeSelectOption(o).label)
    orderedKeys = [
      ...optionLabels.filter(l => groupMap.has(l)),
      ...[...groupMap.keys()].filter(k => k !== '' && !optionLabels.includes(k)),
      ...([''].filter(k => groupMap.has(k))),
    ]
  } else {
    orderedKeys = [
      ...[...groupMap.keys()].filter(k => k !== ''),
      ...([''].filter(k => groupMap.has(k))),
    ]
  }

  return orderedKeys.map(k => groupMap.get(k)!).filter(Boolean)
})

// ── Aggregation helpers ───────────────────────────────────────────────────────

const AGG_BADGES: Record<AggregationType, string> = {
  none: '', count: 'CNT', sum: 'SUM', avg: 'AVG', min: 'MIN', max: 'MAX',
}

function getAggregationFn(colKey: string): AggregationType {
  return (activeView.value?.columnAggregations?.[colKey] as AggregationType | undefined) ?? 'none'
}

function getAggregationBadge(fn: AggregationType): string { return AGG_BADGES[fn] ?? '' }

function _formatAggNum(n: number, schema: PropertySchema | null): string {
  const raw = Number.isInteger(n) ? String(n) : n.toFixed(2).replace(/\.?0+$/, '')
  if (schema?.type === 'number' && (schema.config?.format as string | undefined) === 'euro') {
    return `${raw} \u20ac`
  }
  return raw
}

function computeAggregation(
  entries: DatabaseEntry[],
  colKey: string,
  schema: PropertySchema | null,
  fn: AggregationType,
): string {
  if (fn === 'none') return ''
  if (fn === 'count') {
    const nonEmpty = entries.filter(e => {
      if (colKey === NAME_COL_KEY) return !!((e.content?.title as string | undefined) ?? '').trim()
      return e.values[schema!.id] != null
    })
    return String(nonEmpty.length)
  }
  const numbers: number[] = []
  for (const entry of entries) {
    let n: number | null = null
    if (schema?.type === 'number') {
      const val = entry.values[schema.id]
      const raw = val?.number
      if (typeof raw === 'number' && !isNaN(raw)) n = raw
    } else if (schema?.type === 'formula') {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const val = entry.values[schema.id] as any
      if (typeof val?.result === 'number' && !isNaN(val.result)) n = val.result
    }
    if (n !== null) numbers.push(n)
  }
  if (numbers.length === 0) return ''
  switch (fn) {
    case 'sum': return _formatAggNum(numbers.reduce((a, b) => a + b, 0), schema)
    case 'avg': return _formatAggNum(numbers.reduce((a, b) => a + b, 0) / numbers.length, schema)
    case 'min': return _formatAggNum(Math.min(...numbers), schema)
    case 'max': return _formatAggNum(Math.max(...numbers), schema)
    default:    return ''
  }
}

// ── Wrap column toggle ────────────────────────────────────────────────────────

// Column types whose cells render chips (relation family + rollup). These
// manage their own chip layout via a per-property setting, so the
// per-view line-wrap header button is not offered for them.
const CHIP_COLUMN_TYPES = new Set(['relation', 'parent_item', 'sub_item', 'rollup'])

function supportsLineWrap(schema: PropertySchema | null | undefined): boolean {
  return !!schema && !CHIP_COLUMN_TYPES.has(schema.type)
}

function isWrapped(colKey: string): boolean {
  return (activeView.value?.wrapColumns ?? []).includes(colKey)
}

async function toggleWrapColumn(colKey: string): Promise<void> {
  const view = activeView.value
  if (!view) return
  const current = view.wrapColumns ?? []
  view.wrapColumns = current.includes(colKey)
    ? current.filter(k => k !== colKey)
    : [...current, colKey]
  await saveViews()
}

async function addGroupRow(groupValue: Record<string, unknown> | null): Promise<void> {
  if (isAddingRow.value) return
  isAddingRow.value = true
  try {
    const { values, name } = deriveInitialValuesFromFilters()
    const created          = await dbStore.createEntry(props.blockId)
    const merged: Record<string, Record<string, unknown>> = { ...values }
    const groupSchemaId = activeView.value?.groupBySchemaId
    if (groupValue && groupSchemaId) merged[groupSchemaId] = groupValue
    if (Object.keys(merged).length > 0) {
      await Promise.all(
        Object.entries(merged).map(([schemaId, value]) =>
          dbStore.upsertValue(props.blockId, created.id, schemaId, value),
        ),
      )
    }
    await queryFromActiveView()
    editingNameId.value = created.id
    nameDraft.value     = name ?? ''
    await nextTick()
    const input = document.querySelector<HTMLElement>(`[data-entry-id="${created.id}"] .db__cell-input`)
    input?.scrollIntoView({ block: 'nearest' })
    input?.focus()
  } finally {
    isAddingRow.value = false
  }
}
</script>

<template>
  <div class="db" :class="{ 'db--inline': inline }" @click="closeAllPanels">
    <!-- ── Loading ─────────────────────────────────────────────────────────── -->
    <div v-if="isLoading" class="db__loading">
      <Icon icon="mdi:loading" class="db__spinner" width="24" height="24" />
    </div>

    <template v-else>
      <!-- ── Database title (hidden in inline mode) ────────────────────── -->
      <div v-if="!inline" class="db__title-wrap">
        <div class="db__title-icon-wrap">
          <button
            class="db__title-icon-btn"
            :title="t('main.addIcon')"
            @click.stop="showIconPicker = !showIconPicker"
          >
            <Icon :icon="block?.icon ?? 'mdi:table-large'" class="db__title-icon" width="28" height="28" />
          </button>
          <IconPicker
            v-if="showIconPicker"
            :model-value="block?.icon ?? null"
            @update:model-value="onIconUpdate"
            @close="showIconPicker = false"
          />
        </div>
        <input
          class="db__title-input"
          :value="block?.content?.title ?? ''"
          :placeholder="t('main.untitled')"
          @vue:mounted="onTitleMount"
          @input="titleDraft = ($event.target as HTMLInputElement).value"
          @blur="saveTitle"
        />
      </div>

      <!-- ── View tabs ──────────────────────────────────────────────────── -->
      <div class="db__tabs" @click.stop>
        <div
          v-for="view in views"
          :key="view.id"
          class="db__tab"
          :class="{
            'db__tab--active':    view.id === activeViewId,
            'db__tab--dragging':  tabDragId === view.id,
            'db__tab--drag-over': tabDropId === view.id,
          }"
          draggable="true"
          @dragstart="onTabDragStart($event, view.id)"
          @dragover="onTabDragOver($event, view.id)"
          @dragleave="onTabDragLeave(view.id)"
          @drop="onTabDrop($event, view.id)"
          @dragend="onTabDragEnd"
        >
          <input
            v-if="renamingViewId === view.id"
            class="db__tab-rename-input"
            v-model="viewNameDraft"
            @blur="commitRenameView(view.id)"
            @keydown.enter.prevent="commitRenameView(view.id)"
            @keydown.escape.prevent="cancelRenameView"
            @click.stop
          />
          <span
            v-else
            class="db__tab-label"
            @click="switchView(view.id)"
            @dblclick.stop="startRenameView(view.id)"
          >
            <Icon :icon="viewTypeIcon(view.viewType)" width="12" height="12" class="db__tab-type-icon" />
            {{ view.name }}
          </span>
          <div class="db__tab-menu-wrap">
            <button
              class="db__tab-menu"
              :title="t('db.views.viewMenu')"
              @click.stop="openViewTabMenu(view.id, $event)"
            >
              <Icon icon="mdi:dots-grid" width="12" height="12" />
            </button>
          </div>
        </div>

        <button class="db__tabs-add" :title="t('db.views.addView')" @click.stop="addView">
          <Icon icon="mdi:plus" width="14" height="14" />
        </button>
      </div>

      <!-- ── Toolbar ────────────────────────────────────────────────────── -->
      <div class="db__toolbar" @click.stop>
        <div class="db__toolbar-left">

          <!-- Filter -->
          <div class="db__toolbar-item">
            <button
              class="db__toolbar-btn"
              :class="{ 'db__toolbar-btn--active': showFilterPanel || filterCount > 0 }"
              @click.stop="showFilterPanel = !showFilterPanel; showSortPanel = false; showExportMenu = false"
            >
              <Icon icon="mdi:filter-outline" width="14" height="14" />
              {{ t('db.filter.title') }}
              <span v-if="filterCount > 0" class="db__toolbar-badge">{{ filterCount }}</span>
            </button>
            <FilterPanel
              v-if="showFilterPanel"
              :active-view="activeView"
              :schemas="schemas"
              :displayed-entries="displayedEntries"
              :name-col-key="NAME_COL_KEY"
              @group-conjunction-change="onGroupConjunctionChange"
              @remove-group="removeGroup"
              @filter-schema-change="onFilterSchemaChange"
              @filter-operator-change="onFilterOperatorChange"
              @filter-value-change="onFilterValueChange"
              @filter-value2-change="onFilterValue2Change"
              @filter-date-mode-change="onFilterDateModeChange"
              @filter-date-offset-change="onFilterDateOffsetChange"
              @remove-filter="removeFilter"
              @add-filter="addFilter"
              @add-group="addGroup"
            />
          </div>

          <!-- Sort -->
          <div class="db__toolbar-item">
            <button
              class="db__toolbar-btn"
              :class="{ 'db__toolbar-btn--active': showSortPanel || sortCount > 0 }"
              @click.stop="showSortPanel = !showSortPanel; showFilterPanel = false; showExportMenu = false"
            >
              <Icon icon="mdi:sort" width="14" height="14" />
              {{ t('db.sort.title') }}
              <span v-if="sortCount > 0" class="db__toolbar-badge">{{ sortCount }}</span>
            </button>
            <SortPanel
              v-if="showSortPanel"
              :active-view="activeView"
              :schemas="schemas"
              :name-col-key="NAME_COL_KEY"
              @add-sort="addSort"
              @remove-sort="removeSort"
              @sort-schema-change="onSortSchemaChange"
              @sort-direction-change="onSortDirectionChange"
            />
          </div>

          <!-- View settings -->
          <div class="db__toolbar-item">
            <button
              class="db__toolbar-btn"
              :title="t('db.viewSettings.title')"
              @click.stop="openViewSettings(activeViewId, $event)"
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
              @click.stop="showAutomationsModal = !showAutomationsModal; showFilterPanel = false; showSortPanel = false; showExportMenu = false"
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
              @click.stop="showTemplatesModal = !showTemplatesModal; showFilterPanel = false; showSortPanel = false; showExportMenu = false; showAutomationsModal = false"
            >
              <Icon icon="mdi:file-document-multiple-outline" width="14" height="14" />
              {{ t('db.templates.title') }}
            </button>
            <!-- Template manager dropdown -->
            <div v-if="showTemplatesModal" class="db__panel db__panel--template-manager" @click.stop>
              <TemplateManagerPanel
                :database-id="blockId"
                @edit-template="(id) => { editingTemplateId = id; showTemplatesModal = false }"
                @close="showTemplatesModal = false"
              />
            </div>
          </div>

        </div>

        <div class="db__toolbar-right">
          <!-- Name search -->
          <div class="db__toolbar-item">
            <button
              v-if="!nameSearchActive"
              class="db__toolbar-btn db__toolbar-btn--icon"
              @mouseenter="showTip($event, t('db.search.open'))"
              @mouseleave="hideTip"
              @click.stop="hideTip(); toggleNameSearch()"
            >
              <Icon icon="mdi:magnify" width="14" height="14" />
            </button>
            <div v-else class="db__search-field">
              <Icon icon="mdi:magnify" width="14" height="14" class="db__search-icon" />
              <input
                ref="searchInputEl"
                v-model="nameSearchQuery"
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
          <div v-if="!activeView || activeView.viewType === 'table'" class="db__toolbar-item">
            <button
              class="db__toolbar-btn db__toolbar-btn--new-entry db__toolbar-btn--icon"
              :disabled="isAddingRow"
              @mouseenter="showTip($event, hasMore ? t('db.addRowLimitHint') : t('db.addRow'))"
              @mouseleave="hideTip"
              @click.stop="hideTip(); addRow()"
            >
              <Icon icon="mdi:plus" width="14" height="14" />
            </button>
          </div>

          <!-- Export -->
          <div class="db__toolbar-item">
            <button
              class="db__toolbar-btn db__toolbar-btn--icon"
              @mouseenter="showTip($event, t('db.export.title'))"
              @mouseleave="hideTip"
              @click.stop="hideTip(); showExportMenu = !showExportMenu; showFilterPanel = false; showSortPanel = false"
            >
              <Icon icon="mdi:tray-arrow-up" width="14" height="14" />
            </button>
            <div v-if="showExportMenu" class="db__panel db__panel--right" @click.stop>
              <template v-if="isCalendarView">
                <button class="db__panel-action" @click="exportICS">
                  <Icon icon="mdi:calendar-export-outline" width="14" height="14" />
                  {{ t('db.export.ics') }}
                </button>
              </template>
              <template v-else>
                <button class="db__panel-action" @click="exportCSV">
                  <Icon icon="mdi:file-delimited-outline" width="14" height="14" />
                  {{ t('db.export.csv') }}
                </button>
                <button class="db__panel-action" @click="exportExcel">
                  <Icon icon="mdi:microsoft-excel" width="14" height="14" />
                  {{ t('db.export.excel') }}
                </button>
                <button class="db__panel-action" @click="exportPDF">
                  <Icon icon="mdi:file-pdf-box" width="14" height="14" />
                  {{ t('db.export.pdf') }}
                </button>
              </template>
            </div>
          </div>
        </div>
      </div>

      <!-- ── Table view ──────────────────────────────────────────────────── -->
      <template v-if="!activeView || activeView.viewType === 'table'">
        <div class="db__table-wrap" :class="{ 'db__table-wrap--sticky': stickyHeaderEnabled }">
          <table
            class="db__table"
            :class="{
              'db__table--resizing': resizingKey !== null,
              'db__table--sticky-header': stickyHeaderEnabled,
            }"
          >
            <thead>
              <tr ref="theadRowEl">
                <th
                  class="db__th db__th--handle"
                  :class="{ 'db__th--frozen': handleFrozen }"
                  :style="handleFrozenStyle"
                ></th>
                <template v-for="col in orderedColumns" :key="col.key">
                  <!-- Name column header -->
                  <th
                    v-if="col.key === NAME_COL_KEY"
                    class="db__th db__th--name"
                    :class="{
                      'db__th--drag-over': colDropKey === NAME_COL_KEY,
                      'db__th--dragging':  colDragKey === NAME_COL_KEY,
                      'db__th--frozen':    isFrozenCol(NAME_COL_KEY),
                    }"
                    :style="{ ...colStyle(NAME_COL_KEY), ...frozenColStyle(NAME_COL_KEY) }"
                    draggable="true"
                    @dragstart="onColDragStart($event, NAME_COL_KEY)"
                    @dragover="onColDragOver($event, NAME_COL_KEY)"
                    @dragleave="onColDragLeave(NAME_COL_KEY)"
                    @drop="onColDrop($event, NAME_COL_KEY)"
                    @dragend="onColDragEnd"
                  >
                    <div class="db__th-inner">
                      <span class="db__th-label">
                        <span class="db__th-name">{{ t('db.nameColumn') }}</span>
                      </span>
                      <button
                        class="db__th-btn"
                        :class="{ 'db__th-btn--wrap-active': isWrapped(NAME_COL_KEY) }"
                        :title="isWrapped(NAME_COL_KEY) ? t('db.wrapColumnOff') : t('db.wrapColumnOn')"
                        @click.stop="toggleWrapColumn(NAME_COL_KEY)"
                      >
                        <Icon icon="mdi:wrap" width="13" height="13" />
                      </button>
                    </div>
                    <span
                      class="db__th-resize"
                      :class="{ 'db__th-resize--active': resizingKey === NAME_COL_KEY }"
                      @pointerdown.stop.prevent="(e) => startResize(e, NAME_COL_KEY, (e.currentTarget as HTMLElement).closest('th') as HTMLElement)"
                    />
                  </th>

                  <!-- Schema column header -->
                  <th
                    v-else
                    class="db__th"
                    :class="{
                      'db__th--drag-over': colDropKey === col.key,
                      'db__th--dragging':  colDragKey === col.key,
                      'db__th--frozen':    isFrozenCol(col.key),
                    }"
                    :style="{ ...colStyle(col.key), ...frozenColStyle(col.key) }"
                    draggable="true"
                    @dragstart="onColDragStart($event, col.key)"
                    @dragover="onColDragOver($event, col.key)"
                    @dragleave="onColDragLeave(col.key)"
                    @drop="onColDrop($event, col.key)"
                    @dragend="onColDragEnd"
                  >
                    <div class="db__th-inner">
                      <span class="db__th-label">
                        <div class="db__th-icon-wrap">
                          <button
                            class="db__th-icon-btn"
                            :title="t('db.changeIcon')"
                            @click.stop="openSchemaIconPicker(col.schema!, $event)"
                          >
                            <Icon :icon="getSchemaIcon(col.schema!)" width="14" height="14" class="db__th-type-icon" />
                          </button>
                          <IconPicker
                            v-if="schemaIconPickerKey === col.schema!.id"
                            :model-value="(col.schema!.config?.icon as string | null) ?? null"
                            :trigger-rect="schemaIconPickerRect"
                            @update:model-value="(icon) => onSchemaIconUpdate(col.schema!, icon)"
                            @close="schemaIconPickerKey = null"
                          />
                        </div>
                        <span
                          class="db__th-name"
                          @click="openSettings(col.schema!)"
                          @mouseenter="showColumnTip($event, col.schema!)"
                          @mouseleave="hideTip"
                        >{{ col.schema!.name }}</span>
                      </span>
                      <template v-if="deletingSchemaId === col.key">
                        <button class="db__th-btn db__th-btn--confirm" :title="t('db.deleteColumnConfirm')" @click.stop="confirmDeleteSchema(col.key)">
                          <Icon icon="mdi:check" width="13" height="13" />
                        </button>
                        <button class="db__th-btn" :title="t('actions.cancel')" @click.stop="cancelDeleteSchema">
                          <Icon icon="mdi:close" width="13" height="13" />
                        </button>
                      </template>
                      <button
                        v-else
                        class="db__th-btn db__th-btn--delete"
                        :title="t('db.deleteColumn')"
                        @click.stop="promptDeleteSchema(col.key)"
                      >
                        <Icon icon="mdi:trash-can-outline" width="13" height="13" />
                      </button>
                      <button
                        v-if="deletingSchemaId !== col.key && supportsLineWrap(col.schema)"
                        class="db__th-btn"
                        :class="{ 'db__th-btn--wrap-active': isWrapped(col.key) }"
                        :title="isWrapped(col.key) ? t('db.wrapColumnOff') : t('db.wrapColumnOn')"
                        @click.stop="toggleWrapColumn(col.key)"
                      >
                        <Icon icon="mdi:wrap" width="13" height="13" />
                      </button>
                    </div>
                    <span
                      class="db__th-resize"
                      :class="{ 'db__th-resize--active': resizingKey === col.key }"
                      @pointerdown.stop.prevent="(e) => startResize(e, col.key, (e.currentTarget as HTMLElement).closest('th') as HTMLElement)"
                    />
                  </th>
                </template>
                <th class="db__th db__th--add">
                  <button class="db__add-col-btn" :title="t('db.addSchema.title')" @click="showAddSchema = true">
                    <Icon icon="mdi:plus" width="16" height="16" />
                  </button>
                </th>
              </tr>
            </thead>

            <!-- ── Flat (ungrouped) body ──────────────────────────────── -->
            <tbody v-if="!isGrouped">
              <tr
                v-for="{ entry, depth } in flatTreeEntries"
                :key="entry.id"
                :data-entry-id="entry.id"
                :style="{ '--sub-depth': depth }"
                class="db__row"
                :class="{
                  'db__row--drop-above': getRowDropState(entry.id).above,
                  'db__row--drop-below': getRowDropState(entry.id).below,
                  'db__row--drop-onto':  getRowDropState(entry.id).onto,
                  'db__row--sub-item':   depth > 0,
                }"
                @dragstart="onRowDragStart($event, entry)"
                @dragend="onRowDragEnd(entry)"
                @dragover="onRowDragOver($event, entry)"
                @dragleave="clearRowDropState(entry.id)"
                @drop="onRowDrop($event, entry)"
              >
                <td
                  class="db__td db__td--handle"
                  :class="{ 'db__td--frozen': handleFrozen }"
                  :style="handleFrozenStyle"
                  @dragover.stop.prevent
                  @drop.stop
                  @click.stop
                >
                  <span class="db__row-handle" draggable="true" @click.stop="openRowContextMenu($event, entry)">
                    <Icon icon="mdi:drag" width="13" height="13" />
                  </span>
                </td>
                <template v-for="col in orderedColumns" :key="col.key">
                  <td
                    v-if="col.key === NAME_COL_KEY"
                    class="db__td db__td--name"
                    :class="{ 'db__td--wrap': isWrapped(NAME_COL_KEY), 'db__td--frozen': isFrozenCol(NAME_COL_KEY) }"
                    :style="frozenColStyle(NAME_COL_KEY)"
                  >
                    <input
                      v-if="editingNameId === entry.id"
                      v-model="nameDraft"
                      class="db__cell-input"
                      :style="depth > 0 ? { paddingLeft: (depth * 20 + 6) + 'px' } : {}"
                      @blur="saveNameEdit(entry)"
                      @keydown.enter.prevent="saveNameEdit(entry)"
                      @keydown.escape.prevent="editingNameId = null"
                      @vue:mounted="($el: HTMLInputElement) => $el.focus()"
                    />
                    <div v-else class="db__name-cell">
                      <!-- Sub-item depth indent -->
                      <span
                        v-if="depth > 0"
                        class="db__sub-indent"
                        :style="{ width: (depth * 20) + 'px' }"
                        aria-hidden="true"
                      />
                      <!-- Fold toggle: only visible when entry has subitems -->
                      <button
                        v-if="subItemChildrenMap.has(entry.id)"
                        class="db__fold-btn"
                        :class="{ 'db__fold-btn--folded': foldedEntries.has(entry.id) }"
                        :title="foldedEntries.has(entry.id) ? 'Expand' : 'Collapse'"
                        @click.stop="toggleFold(entry.id)"
                      >
                        <Icon icon="mdi:chevron-down" width="12" height="12" />
                      </button>
                      <!-- Placeholder keeps alignment when no fold button is shown -->
                      <span v-else class="db__fold-placeholder" aria-hidden="true" />
                      <div class="db__entry-icon-wrap">
                        <button
                          class="db__entry-icon-btn"
                          :title="t('main.addIcon')"
                          @click.stop="openEntryIconPicker(entry, $event)"
                        >
                          <Icon :icon="entryIcon(entry)" class="db__entry-icon" width="14" height="14" />
                        </button>
                        <IconPicker
                          v-if="entryIconPickerEntryId === entry.id"
                          :model-value="entryIcon(entry)"
                          :trigger-rect="entryIconPickerRect"
                          @update:model-value="onEntryIconUpdate"
                          @close="entryIconPickerEntryId = null"
                        />
                      </div>
                      <span
                        class="db__entry-name"
                        :class="{ 'db__entry-name--empty': !entry.content?.title }"
                        :title="entry.content?.title ? String(entry.content.title) : undefined"
                        @click="startNameEdit(entry)"
                      >
                        {{ entry.content?.title || t('main.untitled') }}
                      </span>
                      <button class="db__open-btn" :title="t('db.openEntry')" @click="openEntry(entry)">
                        <Icon icon="mdi:arrow-top-right" width="12" height="12" />
                      </button>
                    </div>
                  </td>
                  <td v-else class="db__td" :class="{ 'db__td--wrap': isWrapped(col.key), 'db__td--frozen': isFrozenCol(col.key) }" :style="frozenColStyle(col.key)" @click="onCellClick(entry, col.schema!)">
                    <CheckboxCell   v-if="col.schema!.type === 'checkbox'"                                                               :entry="entry" :schema="col.schema!" :database-id="blockId" />
                    <SelectCell     v-else-if="col.schema!.type === 'select' && (col.schema!.config?.mode ?? 'single') === 'single'"     :entry="entry" :schema="col.schema!" :database-id="blockId" :is-active="isActiveCell(entry.id, col.schema!.id)" @activate="setActiveCell(entry.id, col.schema!.id)" @deactivate="clearActiveCell" />
                    <MultiSelectCell v-else-if="col.schema!.type === 'select' && col.schema!.config?.mode === 'multiple'"                :entry="entry" :schema="col.schema!" :database-id="blockId" :is-active="isActiveCell(entry.id, col.schema!.id)" @activate="setActiveCell(entry.id, col.schema!.id)" @deactivate="clearActiveCell" />
                    <DateCell       v-else-if="col.schema!.type === 'date'"                                                              :entry="entry" :schema="col.schema!" :database-id="blockId" :is-active="isActiveCell(entry.id, col.schema!.id)" @activate="setActiveCell(entry.id, col.schema!.id)" @deactivate="clearActiveCell" @saved="queryFromActiveView" />
                    <RelationCell   v-else-if="col.schema!.type === 'relation'"                                                          :schema="col.schema!" :entry="entry" :database-id="blockId" @change="handleRelationChange(entry, col.schema!, $event)" />
                    <RelationCell   v-else-if="col.schema!.type === 'parent_item'"                                                       :schema="col.schema!" :entry="entry" :database-id="blockId" @change="handleRelationChange(entry, col.schema!, $event)" />
                    <RelationCell   v-else-if="col.schema!.type === 'sub_item'"                                                          :schema="col.schema!" :entry="entry" :database-id="blockId" :readonly="true" />
                    <LinkCell       v-else-if="col.schema!.type === 'email' || col.schema!.type === 'phone' || col.schema!.type === 'url'" :entry="entry" :schema="col.schema!" :database-id="blockId" :is-active="isActiveCell(entry.id, col.schema!.id)" @activate="setActiveCell(entry.id, col.schema!.id)" @deactivate="clearActiveCell" />
                    <FileCell       v-else-if="col.schema!.type === 'file'"                                                              :entry="entry" :schema="col.schema!" :database-id="blockId" :is-active="isActiveCell(entry.id, col.schema!.id)" @activate="setActiveCell(entry.id, col.schema!.id)" @deactivate="clearActiveCell" />
                    <ReadonlyCell   v-else-if="isReadonlyPropertyType(col.schema!.type)"                                                 :entry="entry" :schema="col.schema!" />
                    <RollupCell     v-else-if="col.schema!.type === 'rollup'"                                                            :entry="entry" :schema="col.schema!" />
                    <FormulaCell    v-else-if="col.schema!.type === 'formula'"                                                           :entry="entry" :schema="col.schema!" />
                    <TextCell       v-else                                                                                                :entry="entry" :schema="col.schema!" :database-id="blockId" :is-active="isActiveCell(entry.id, col.schema!.id)" @activate="setActiveCell(entry.id, col.schema!.id)" @deactivate="clearActiveCell" />
                  </td>
                </template>
                <td class="db__td db__td--add"></td>
              </tr>
            </tbody>

            <!-- Flat aggregation footer -->
            <tfoot v-if="!isGrouped">
              <tr class="db__aggregation-row">
                <td
                  class="db__td db__td--handle db__agg-handle-cell"
                  :class="{ 'db__aggregation-cell--frozen': handleFrozen }"
                  :style="handleFrozenStyle"
                >
                  <button class="db__group-add-btn" :disabled="isAddingRow" @mouseenter="hasMore ? showTip($event, t('db.addRowLimitHint')) : undefined" @mouseleave="hideTip" @click="addRow">
                    <Icon icon="mdi:plus" width="13" height="13" />
                    {{ t('db.addRowShort') }}
                  </button>
                </td>
                <template v-for="col in orderedColumns" :key="col.key">
                  <td
                    class="db__aggregation-cell"
                    :class="{ 'db__aggregation-cell--frozen': isFrozenCol(col.key) }"
                    :style="{ ...colStyle(col.key), ...frozenColStyle(col.key) }"
                    @click.stop="aggPickerKey = aggPickerKey === ('flat:' + col.key) ? null : ('flat:' + col.key)"
                  >
                    <div class="db__agg-inner">
                      <template v-if="getAggregationFn(col.key) !== 'none'">
                        <span class="db__agg-badge">{{ getAggregationBadge(getAggregationFn(col.key)) }}</span>
                        <span class="db__agg-value">{{ computeAggregation(filteredAndSortedEntries, col.key, col.schema, getAggregationFn(col.key)) }}</span>
                      </template>
                      <span v-else class="db__agg-plus"><Icon icon="mdi:plus" width="11" height="11" /></span>
                      <div v-if="aggPickerKey === ('flat:' + col.key)" class="db__agg-picker" @click.stop>
                        <button
                          v-for="opt in aggOptionsForCol(col.schema)"
                          :key="opt"
                          class="db__agg-picker-opt"
                          :class="{ 'db__agg-picker-opt--active': getAggregationFn(col.key) === opt }"
                          @click="setAggregation(col.key, opt)"
                        >
                          {{ opt === 'none' ? t('db.viewSettings.aggNone') : getAggregationBadge(opt) }}
                        </button>
                      </div>
                    </div>
                  </td>
                </template>
                <td class="db__td db__td--add"></td>
              </tr>
            </tfoot>

            <!-- ── Grouped view ───────────────────────────────────────── -->
            <template v-else v-for="group in tableGroups" :key="group.key">
              <tbody class="db__group-header-body">
                <tr class="db__group-header-row">
                  <td
                    class="db__td db__td--handle db__group-header-handle"
                    :class="{ 'db__td--frozen': handleFrozen }"
                    :style="handleFrozenStyle"
                  ></td>
                  <td :colspan="orderedColumns.length + 1" class="db__group-header-cell">
                    <Icon icon="mdi:chevron-right" width="12" height="12" class="db__group-header-icon" />
                    <span class="db__group-header-label">{{ group.label }}</span>
                  </td>
                </tr>
              </tbody>

              <tbody class="db__group-body">
                <tr
                  v-for="entry in group.entries"
                  :key="entry.id"
                  :data-entry-id="entry.id"
                  class="db__row"
                  :class="{
                    'db__row--drop-above': getRowDropState(entry.id).above,
                    'db__row--drop-below': getRowDropState(entry.id).below,
                  }"
                  @dragstart="onRowDragStart($event, entry)"
                  @dragend="onRowDragEnd(entry)"
                  @dragover="onRowDragOver($event, entry)"
                  @dragleave="clearRowDropState(entry.id)"
                  @drop="onRowDrop($event, entry)"
                >
                  <td
                    class="db__td db__td--handle"
                    :class="{ 'db__td--frozen': handleFrozen }"
                    :style="handleFrozenStyle"
                    @dragover.stop.prevent
                    @drop.stop
                    @click.stop
                  >
                    <span class="db__row-handle" draggable="true" @click.stop="openRowContextMenu($event, entry)">
                      <Icon icon="mdi:drag" width="13" height="13" />
                    </span>
                  </td>
                  <template v-for="col in orderedColumns" :key="col.key">
                    <td
                      v-if="col.key === NAME_COL_KEY"
                      class="db__td db__td--name"
                      :class="{ 'db__td--wrap': isWrapped(NAME_COL_KEY), 'db__td--frozen': isFrozenCol(NAME_COL_KEY) }"
                      :style="frozenColStyle(NAME_COL_KEY)"
                    >
                      <input
                        v-if="editingNameId === entry.id"
                        v-model="nameDraft"
                        class="db__cell-input"
                        @blur="saveNameEdit(entry)"
                        @keydown.enter.prevent="saveNameEdit(entry)"
                        @keydown.escape.prevent="editingNameId = null"
                        @vue:mounted="($el: HTMLInputElement) => $el.focus()"
                      />
                      <div v-else class="db__name-cell">
                        <div class="db__entry-icon-wrap">
                          <button
                            class="db__entry-icon-btn"
                            :title="t('main.addIcon')"
                            @click.stop="openEntryIconPicker(entry, $event)"
                          >
                            <Icon :icon="entryIcon(entry)" class="db__entry-icon" width="14" height="14" />
                          </button>
                          <IconPicker
                            v-if="entryIconPickerEntryId === entry.id"
                            :model-value="entryIcon(entry)"
                            :trigger-rect="entryIconPickerRect"
                            @update:model-value="onEntryIconUpdate"
                            @close="entryIconPickerEntryId = null"
                          />
                        </div>
                        <span
                          class="db__entry-name"
                          :class="{ 'db__entry-name--empty': !entry.content?.title }"
                          :title="entry.content?.title ? String(entry.content.title) : undefined"
                          @click="startNameEdit(entry)"
                        >
                          {{ entry.content?.title || t('main.untitled') }}
                        </span>
                        <button class="db__open-btn" :title="t('db.openEntry')" @click="openEntry(entry)">
                          <Icon icon="mdi:arrow-top-right" width="12" height="12" />
                        </button>
                      </div>
                    </td>
                    <td v-else class="db__td" :class="{ 'db__td--wrap': isWrapped(col.key), 'db__td--frozen': isFrozenCol(col.key) }" :style="frozenColStyle(col.key)" @click="onCellClick(entry, col.schema!)">
                      <CheckboxCell   v-if="col.schema!.type === 'checkbox'"                                                               :entry="entry" :schema="col.schema!" :database-id="blockId" />
                      <SelectCell     v-else-if="col.schema!.type === 'select' && (col.schema!.config?.mode ?? 'single') === 'single'"     :entry="entry" :schema="col.schema!" :database-id="blockId" :is-active="isActiveCell(entry.id, col.schema!.id)" @activate="setActiveCell(entry.id, col.schema!.id)" @deactivate="clearActiveCell" />
                      <MultiSelectCell v-else-if="col.schema!.type === 'select' && col.schema!.config?.mode === 'multiple'"                :entry="entry" :schema="col.schema!" :database-id="blockId" :is-active="isActiveCell(entry.id, col.schema!.id)" @activate="setActiveCell(entry.id, col.schema!.id)" @deactivate="clearActiveCell" />
                      <DateCell       v-else-if="col.schema!.type === 'date'"                                                              :entry="entry" :schema="col.schema!" :database-id="blockId" :is-active="isActiveCell(entry.id, col.schema!.id)" @activate="setActiveCell(entry.id, col.schema!.id)" @deactivate="clearActiveCell" @saved="queryFromActiveView" />
                      <RelationCell   v-else-if="col.schema!.type === 'relation'"                                                          :schema="col.schema!" :entry="entry" :database-id="blockId" @change="handleRelationChange(entry, col.schema!, $event)" />
                      <RelationCell   v-else-if="col.schema!.type === 'parent_item'"                                                       :schema="col.schema!" :entry="entry" :database-id="blockId" @change="handleRelationChange(entry, col.schema!, $event)" />
                      <RelationCell   v-else-if="col.schema!.type === 'sub_item'"                                                          :schema="col.schema!" :entry="entry" :database-id="blockId" :readonly="true" />
                      <LinkCell       v-else-if="col.schema!.type === 'email' || col.schema!.type === 'phone' || col.schema!.type === 'url'" :entry="entry" :schema="col.schema!" :database-id="blockId" :is-active="isActiveCell(entry.id, col.schema!.id)" @activate="setActiveCell(entry.id, col.schema!.id)" @deactivate="clearActiveCell" />
                      <FileCell       v-else-if="col.schema!.type === 'file'"                                                              :entry="entry" :schema="col.schema!" :database-id="blockId" :is-active="isActiveCell(entry.id, col.schema!.id)" @activate="setActiveCell(entry.id, col.schema!.id)" @deactivate="clearActiveCell" />
                      <ReadonlyCell   v-else-if="isReadonlyPropertyType(col.schema!.type)"                                                 :entry="entry" :schema="col.schema!" />
                      <RollupCell     v-else-if="col.schema!.type === 'rollup'"                                                            :entry="entry" :schema="col.schema!" />
                      <FormulaCell    v-else-if="col.schema!.type === 'formula'"                                                           :entry="entry" :schema="col.schema!" />
                      <TextCell       v-else                                                                                                :entry="entry" :schema="col.schema!" :database-id="blockId" :is-active="isActiveCell(entry.id, col.schema!.id)" @activate="setActiveCell(entry.id, col.schema!.id)" @deactivate="clearActiveCell" />
                    </td>
                  </template>
                  <td class="db__td db__td--add"></td>
                </tr>

                <!-- Per-group aggregation footer -->
                <tr class="db__aggregation-row">
                  <td
                    class="db__td db__td--handle db__agg-handle-cell"
                    :class="{ 'db__aggregation-cell--frozen': handleFrozen }"
                    :style="handleFrozenStyle"
                  >
                    <button class="db__group-add-btn" :disabled="isAddingRow" @mouseenter="hasMore ? showTip($event, t('db.addRowLimitHint')) : undefined" @mouseleave="hideTip" @click="addGroupRow(group.groupValue)">
                      <Icon icon="mdi:plus" width="13" height="13" />
                      {{ t('db.addRowShort') }}
                    </button>
                  </td>
                  <template v-for="col in orderedColumns" :key="col.key">
                    <td
                      class="db__aggregation-cell"
                      :class="{ 'db__aggregation-cell--frozen': isFrozenCol(col.key) }"
                      :style="{ ...colStyle(col.key), ...frozenColStyle(col.key) }"
                      @click.stop="aggPickerKey = aggPickerKey === (group.key + ':' + col.key) ? null : (group.key + ':' + col.key)"
                    >
                      <div class="db__agg-inner">
                        <template v-if="getAggregationFn(col.key) !== 'none'">
                          <span class="db__agg-badge">{{ getAggregationBadge(getAggregationFn(col.key)) }}</span>
                          <span class="db__agg-value">{{ computeAggregation(group.entries, col.key, col.schema, getAggregationFn(col.key)) }}</span>
                        </template>
                        <span v-else class="db__agg-plus"><Icon icon="mdi:plus" width="11" height="11" /></span>
                        <div v-if="aggPickerKey === (group.key + ':' + col.key)" class="db__agg-picker" @click.stop>
                          <button
                            v-for="opt in aggOptionsForCol(col.schema)"
                            :key="opt"
                            class="db__agg-picker-opt"
                            :class="{ 'db__agg-picker-opt--active': getAggregationFn(col.key) === opt }"
                            @click="setAggregation(col.key, opt)"
                          >
                            {{ opt === 'none' ? t('db.viewSettings.aggNone') : getAggregationBadge(opt) }}
                          </button>
                        </div>
                      </div>
                    </td>
                  </template>
                  <td class="db__aggregation-cell"></td>
                </tr>
              </tbody>
            </template>
          </table>
        </div>
      </template>

      <!-- ── Calendar view ─────────────────────────────────────────────── -->
      <CalendarView
        v-else-if="activeView?.viewType === 'calendar' && activeView.calendarSubtype !== 'agenda'"
        :entries="filteredAndSortedEntries"
        :schemas="schemas"
        :view="activeView"
        :database-id="blockId"
        @open-entry="openEntry"
        @add-on-date="addRowOnDate"
        @update-view="onViewUpdated"
        @refresh="queryFromActiveView"
      />

      <!-- ── Agenda view ────────────────────────────────────────────────── -->
      <AgendaView
        v-else-if="activeView?.viewType === 'calendar' && activeView.calendarSubtype === 'agenda'"
        :entries="filteredAndSortedEntries"
        :schemas="schemas"
        :view="activeView"
        :database-id="blockId"
        @open-entry="openEntry"
        @add-on-date="addRowOnDate"
        @update-view="onViewUpdated"
        @refresh="queryFromActiveView"
      />

      <!-- ── Planned view placeholder ──────────────────────────────────── -->
      <div v-else class="db__view-planned">
        <Icon icon="mdi:wrench-clock-outline" width="32" height="32" />
        <p>{{ t('db.viewSettings.viewPlanned') }}</p>
      </div>

      <!-- ── Load-more bar ─────────────────────────────────────────────── -->
      <div
        v-if="hasMore && !isCalendarView"
        class="db__load-more"
        @click.stop
      >
        <span class="db__load-more-info">{{ t('db.loadMoreInfo', { shown: displayedEntries.length, total: totalEntries }) }}</span>
        <button class="db__load-more-btn" @click="loadMore(50)">+50</button>
        <button class="db__load-more-btn" @click="loadMore(100)">+100</button>
        <button class="db__load-more-btn" @click="loadMore(200)">+200</button>
      </div>
    </template>

    <!-- ── View tab action menu ──────────────────────────────────────────── -->
    <Teleport to="body">
      <div
        v-if="viewTabMenuId"
        class="db__tab-action-menu"
        :style="{ top: viewTabMenuPos.y + 'px', left: viewTabMenuPos.x + 'px' }"
        @click.stop
      >
        <button class="db__tab-action-item" @click.stop="duplicateView(viewTabMenuId)">
          <Icon icon="mdi:content-copy" width="13" height="13" />
          {{ t('actions.duplicate') }}
        </button>
        <button
          v-if="views.length > 1"
          class="db__tab-action-item db__tab-action-item--danger"
          @click.stop="promptDeleteView(viewTabMenuId)"
        >
          <Icon icon="mdi:trash-can-outline" width="13" height="13" />
          {{ t('db.views.deleteView') }}
        </button>
      </div>
    </Teleport>

    <!-- ── View delete confirmation dialog ──────────────────────────────── -->
    <Teleport to="body">
      <div v-if="deleteConfirmViewId" class="db__view-delete-overlay" @click.stop="cancelDeleteView">
        <div class="db__view-delete-dialog" @click.stop>
          <div class="db__view-delete-icon">
            <Icon icon="mdi:alert-outline" width="22" height="22" />
          </div>
          <p class="db__view-delete-title">{{ t('db.views.deleteViewTitle') }}</p>
          <p class="db__view-delete-warning">{{ t('db.views.deleteViewWarning') }}</p>
          <div class="db__view-delete-actions">
            <button class="db__view-delete-cancel" @click="cancelDeleteView">{{ t('actions.cancel') }}</button>
            <button class="db__view-delete-confirm" @click="confirmDeleteView">{{ t('db.views.deleteView') }}</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- ── Row context menu ──────────────────────────────────────────────── -->
    <Teleport to="body">
      <div
        v-if="rowContextMenu.visible"
        ref="rowContextMenuEl"
        class="db__row-ctx-menu"
        :style="{ top: rowContextMenu.y + 'px', left: rowContextMenu.x + 'px' }"
        @click.stop
      >
        <button class="db__row-ctx-item" @click="contextMenuDuplicateEntry">
          <Icon icon="mdi:content-copy" width="13" height="13" />
          {{ t('actions.duplicate') }}
        </button>
        <button class="db__row-ctx-item db__row-ctx-item--danger" @click="contextMenuDeleteEntry">
          <Icon icon="mdi:trash-can-outline" width="13" height="13" />
          {{ t('actions.delete') }}
        </button>
      </div>
    </Teleport>

    <!-- ── View settings modal ────────────────────────────────────────────── -->
    <ViewSettingsModal
      v-if="viewSettingsViewId"
      :view="viewSettingsView!"
      :schemas="schemas"
      :database-id="blockId"
      @close="closeViewSettings"
      @update="onViewUpdated"
    />

    <!-- ── Add schema overlay ─────────────────────────────────────────────── -->
    <AddSchemaPanel
      v-if="showAddSchema"
      :database-id="blockId"
      @close="onAddSchemaPanelClose"
    />

    <!-- ── Property settings modal ───────────────────────────────────────── -->
    <PropertySettingsModal
      v-if="settingsSchema"
      :schema="settingsSchema"
      :database-id="blockId"
      @close="closeSettings"
    />

    <KeyReferenceDeleteDialog
      v-if="keyDeleteSchemaId"
      :references="keyReferences"
      :property-name="keyDeleteSchemaName"
      @confirm="confirmKeyDelete"
      @cancel="cancelKeyDelete"
    />

    <!-- ── Side view panel ────────────────────────────────────────────────── -->
    <SideView
      v-if="sideViewEntryId"
      :database-id="blockId"
      :entry-id="sideViewEntryId"
      @close="closeSideView"
      @refresh="onSideViewRefresh"
    />

    <!-- ── Automations modal ─────────────────────────────────────────────── -->
    <AutomationsModal
      v-if="showAutomationsModal"
      :database-id="blockId"
      :schemas="schemas"
      @close="showAutomationsModal = false"
    />

    <!-- ── Template editor panel ────────────────────────────────────────── -->
    <DatabaseTemplateEditor
      v-if="editingTemplateId"
      :database-id="blockId"
      :template-id="editingTemplateId"
      @close="editingTemplateId = null"
    />

    <!-- ── Limit-hint tooltip ────────────────────────────────────────────── -->
    <Teleport to="body">
      <div
        v-if="tip.visible"
        class="db__tip"
        :class="{ 'db__tip--rich': tip.variant === 'rich' }"
        :style="{ left: tip.x + 'px', top: tip.y + 'px' }"
      ><span class="db__tip-text">{{ tip.text }}</span><span v-if="tip.subtext" class="db__tip-subtext">{{ tip.subtext }}</span></div>
    </Teleport>
  </div>
</template>

<style>
/* Styles live in DatabaseBlock.css (non-scoped, imported below). */
@import '@/assets/DatabaseBlock.css';

/*
 * Prevent the flex algorithm in .main-view from compressing .db to
 * viewport height. With flex-shrink: 1 (default), .main-view's fixed height
 * caused .db — and everything inside it including CalendarView — to be
 * clamped to ~417 px, clipping the lower calendar rows entirely.
 * flex-shrink: 0 lets .db grow to its natural content height; .main-view
 * (overflow-y: auto) handles page-level scrolling for all view types.
 */
.db { flex-shrink: 0; }

/* ── Sub-item row drop zones ─────────────────────────────────────────────── */
.db__row--drop-onto {
  outline: 1.5px solid var(--color-accent);
  outline-offset: -1px;
  background: color-mix(in srgb, var(--color-accent) 6%, transparent);
}

/* ── Sub-item depth indent ───────────────────────────────────────────────── */
.db__sub-indent {
  display: inline-block;
  flex-shrink: 0;
}

/* ── Fold toggle button (sits in the handle cell) ────────────────────────── */
.db__fold-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  background: transparent;
  border: none;
  cursor: pointer;
  color: var(--color-text-muted);
  border-radius: 3px;
  transition: color 0.15s, background 0.15s;
  padding: 0;
}

.db__fold-btn:hover {
  background: var(--color-hover);
  color: var(--color-text);
}

.db__fold-btn--folded svg {
  transform: rotate(-90deg);
  transition: transform 0.15s;
}

.db__fold-btn svg {
  transition: transform 0.15s;
}

/* Placeholder keeps alignment when no fold button present */
.db__fold-placeholder {
  display: inline-flex;
  width: 16px;
  height: 16px;
  flex-shrink: 0;
}
.db__row--sub-item > td {
  background: color-mix(in srgb, var(--color-surface) 40%, var(--color-hover) 60%);
}

.db__row--sub-item:hover > td {
  background: var(--color-hover);
}

/* ── Name search ─────────────────────────────────────────────────────────── */
.db__toolbar-btn--icon {
  padding: 4px 6px;
}

.db__search-field {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 2px 4px 2px 7px;
  border: 1px solid var(--color-border);
  border-radius: 5px;
  background: var(--color-bg);
}

.db__search-icon {
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.db__search-input {
  border: none;
  background: transparent;
  outline: none;
  color: var(--color-text);
  font-size: 0.8125rem;
  width: 150px;
  padding: 2px 0;
}

.db__search-input::placeholder {
  color: var(--color-text-muted);
}

.db__search-clear {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: none;
  cursor: pointer;
  color: var(--color-text-muted);
  border-radius: 4px;
  padding: 2px;
  flex-shrink: 0;
  transition: color 0.15s, background 0.15s;
}

.db__search-clear:hover {
  background: var(--color-hover);
  color: var(--color-text);
}

/* ── Limit-hint tooltip (teleported) ────────────────────────────────────── */
.db__tip {
  position: fixed;
  transform: translate(-50%, calc(-100% - 7px));
  max-width: 260px;
  white-space: normal;
  text-align: center;
  background: var(--color-text);
  color: var(--color-surface);
  font-size: 0.75rem;
  line-height: 1.4;
  padding: 5px 9px;
  border-radius: 5px;
  pointer-events: none;
  z-index: 9999;
}

.db__tip-subtext {
  display: block;
  margin-top: 2px;
  font-style: italic;
  opacity: 0.8;
}

/*
 * Rich variant (column-header tooltip). The base db__tip is an inverted
 * light-on-dark hint; for the larger, multi-line header tooltip that reads as
 * an out-of-place light block in CapyBarca's dark theme, so this variant uses
 * the app's elevated-surface colours instead.
 */
.db__tip--rich {
  background: var(--color-surface);
  color: var(--color-text);
  border: 1px solid var(--color-border);
  text-align: left;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.35);
}

.db__tip--rich .db__tip-subtext {
  color: var(--color-text-muted);
  opacity: 1;
}

/* ── Load-more bar ───────────────────────────────────────────────────────── */
.db__load-more {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 12px;
  border-top: 1px solid var(--color-border);
  font-size: 0.8125rem;
}

.db__load-more-info {
  flex: 1;
  color: var(--color-text-muted);
  font-size: 0.8rem;
}

.db__load-more-btn {
  padding: 2px 10px;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  background: transparent;
  color: var(--color-text-muted);
  font-size: 0.8125rem;
  cursor: pointer;
  transition: background 0.12s, color 0.12s, border-color 0.12s;
}

.db__load-more-btn:hover {
  background: var(--color-hover);
  color: var(--color-text);
  border-color: var(--color-text-muted);
}
</style>

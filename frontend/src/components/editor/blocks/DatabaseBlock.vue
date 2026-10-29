<script setup lang="ts">
/**
 * DatabaseBlock
 *
 * Orchestrates database views, entries, schemas, and interactions.
 * Logic is delegated to focused composables; UI panels are extracted
 * into subcomponents. This file is intentionally the thin integration
 * layer that wires everything together.
 *
 * Views and entries
 * -----------------
 * The views come from the databaseViews store, shared by every block showing
 * this database; which one is active is the choice of this placement (the
 * database block itself, or the linked block passed as ``placementId``).
 * Entries arrive a page at a time from the backend, which applies the view's
 * filters and sorts and resolves every relation on the page. Loading more
 * appends the next batch. After a change, the range already loaded is
 * compared with the server's by id and only the entries missing from it are
 * loaded; changed values of loaded entries are fetched for just those
 * entries. Refreshes of one view run one at a time, and those that arrive
 * meanwhile are merged into one (``useCoalescedRefresh``).
 *
 * Composables
 * -----------
 * useViewManager      – views CRUD, tab drag-reorder, sort management
 * useFilterPanel      – filter-group state + mutations
 * useColumnResize     – column width pointer events
 * useExport           – CSV / XLSX / PDF / ICS export
 * useDatabaseGrouping – group-by and column aggregations
 * useSubItemTree      – sub-item hierarchy, fold state, rows in tree order
 * useStickyColumns    – sticky header and frozen columns
 * useRowDragDrop      – row reorder and sub-item drop
 * useAddRow           – new entries with values derived from the filters
 * useCoalescedRefresh – one refresh at a time, merging those that pile up
 * useQuickPrint       – Quick Print of a row from its handle menu, with the
 *                       view's visible columns in the view's order
 *
 * Subcomponents
 * -------------
 * DatabaseViewTabs – view tabs
 * DatabaseToolbar  – toolbar with name search and export menu
 * FilterPanel      – filter UI (template + styles live in DatabaseBlock.css)
 * SortPanel        – sort UI
 * ViewDescription  – per-view description between toolbar and view content
 *
 * Write access
 * ------------
 * The database's ``can_write`` is provided to everything inside (see
 * ``useEditable``), the cells, calendar and agenda included. Without it the
 * database is shown as its views are set up: switching views, searching by
 * name, folding sub-items, opening entries, loading more and exporting
 * remain; everything that changes entries, properties, views (filters, sorts,
 * column order, widths, wrapping, aggregations, descriptions), automations or
 * templates is not offered. An inline database the user may only read shows
 * a read-only badge in its toolbar; on a page of its own the page shows it.
 */
import { ref, computed, onMounted, onUnmounted, nextTick, watch, type VNode } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { useBlockStore } from '@/stores/blocks'
import { WS_BLOCK_EVENT, WS_RESYNC_EVENT, type BlockEventPayload } from '@/stores/ws'
import {
  useDatabaseStore,
  type PropertySchema,
  type DatabaseEntry,
  type KeyReference,
  type EntriesEventDetails,
  NAME_COL_KEY,
  entriesEventDetails,
} from '@/stores/database'
import { isReadonlyPropertyType } from '@/stores/propertyTypes'
import { provideEditable } from '@/composables/useEditable'

// ── Composables ───────────────────────────────────────────────────────────────
import { useDatabaseViewsStore } from '@/stores/databaseViews'
import { useViewManager } from '@/composables/useViewManager'
import { useFilterPanel } from '@/composables/useFilterPanel'
import { useColumnResize } from '@/composables/useColumnResize'
import { useExport } from '@/composables/useExport'
import {
  useDatabaseGrouping,
  aggregationBadge,
  aggregationOptions,
  computeAggregation,
} from '@/composables/useDatabaseGrouping'
import { useSubItemTree } from '@/composables/useSubItemTree'
import { useStickyColumns } from '@/composables/useStickyColumns'
import { useRowDragDrop } from '@/composables/useRowDragDrop'
import { useAddRow } from '@/composables/useAddRow'
import { useCoalescedRefresh } from '@/composables/useCoalescedRefresh'
import { useQuickPrint } from '@/composables/useQuickPrint'

// ── Subcomponents ─────────────────────────────────────────────────────────────
import DatabaseViewTabs from './subcomponents/DatabaseViewTabs.vue'
import DatabaseToolbar, { type ExportFormat } from './subcomponents/DatabaseToolbar.vue'
import FilterPanel from './subcomponents/FilterPanel.vue'
import SortPanel from './subcomponents/SortPanel.vue'
import ViewDescription from './subcomponents/ViewDescription.vue'

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

// ── Props ─────────────────────────────────────────────────────────────────────

const props = defineProps<{
  blockId: string
  /**
   * Set by BlockContentSection when rendered inline inside a content block.
   * Suppresses the large title / icon header.
   */
  inline?: boolean
  /**
   * The block that shows this database, when it is not the database block
   * itself: a ``linked_database`` block. Each placement keeps its own choice
   * of view.
   */
  placementId?: string
}>()

// ── Dependencies ──────────────────────────────────────────────────────────────

const { t }       = useI18n()
const blockStore  = useBlockStore()
const dbStore     = useDatabaseStore()
const viewsStore  = useDatabaseViewsStore()

/** The block whose choice of view this instance shows and stores. */
const placementId = props.placementId ?? props.blockId

// ── Base state ────────────────────────────────────────────────────────────────

const block   = computed(() => blockStore.blocks[props.blockId])
const editable = provideEditable(block)
const schemas = computed(() => dbStore.getSchemas(props.blockId))
const isLoading = ref(true)

// ── Server-side query state ───────────────────────────────────────────────────

const displayedEntries = ref<DatabaseEntry[]>([])
const totalEntries     = ref(0)

/**
 * Key under which this instance's query result lives in the store. Unique per
 * instance, because the same database can be shown by several blocks at once,
 * each with its own view, filters and page.
 */
const queryResultKey = `${props.blockId}:${crypto.randomUUID()}`

// ── Name search ───────────────────────────────────────────────────────────────

// The toolbar's name search (case-insensitive substring match) runs on the
// server as an additional filter of the view query, so it reaches every entry
// of the database, not just the loaded page, and the load-more bar, the totals
// and the aggregations all reflect the matching set.
const NAME_SEARCH_DEBOUNCE_MS = 250

const nameSearchQuery = ref('')

// filteredAndSortedEntries is the single source the table, calendar, agenda,
// aggregations and export all render from. Filtering, the name search
// included, and sorting happen on the server, so it is the query result.
const filteredAndSortedEntries = computed<DatabaseEntry[]>(() => displayedEntries.value)

let _nameSearchTimer: ReturnType<typeof setTimeout> | null = null

watch(
  () => nameSearchQuery.value.trim(),
  (query, previous) => {
    if (query === previous) return
    if (_nameSearchTimer) clearTimeout(_nameSearchTimer)
    // Clearing the search restores the full view at once; typing is debounced.
    if (!query) {
      _nameSearchTimer = null
      _resetQueryFn()
      return
    }
    _nameSearchTimer = setTimeout(() => {
      _nameSearchTimer = null
      _resetQueryFn()
    }, NAME_SEARCH_DEBOUNCE_MS)
  },
)

// ── Load-more pagination ──────────────────────────────────────────────────────

/** Entries loaded when a view is opened, switched, filtered or sorted. */
const PAGE_SIZE = 100

/**
 * True when the server has more entries than the ones loaded.
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

// ── Composable: view manager ──────────────────────────────────────────────────
// queryFromActiveView is defined AFTER viewManager (it closes over activeView).
// The composable receives a stable function reference that calls the real
// implementation once it is defined, so onQueryNeeded works immediately.

// Stable reference for filter/sort/view-switch initiated loads. These start
// over with the first page, whereas WS-driven refreshes and row operations
// bring the range already loaded up to date.
let _resetQueryFn: () => Promise<void> = async () => {}

const vm = useViewManager({
  databaseId:    props.blockId,
  placementId,
  schemas,
  NAME_COL_KEY,
  onQueryNeeded: () => _resetQueryFn(),
})

const {
  views, activeViewId, activeView,
  renamingViewId, viewNameDraft,
  tabDragId, tabDropId,
  viewTabMenuId, viewTabMenuPos,
  deleteConfirmViewId,
  viewSettingsViewId, viewSettingsView,
  showSortPanel, sortCount,
  saveViews, saveActiveViewId,
  addView,
  startRenameView, commitRenameView, cancelRenameView,
  onTabDragStart, onTabDragOver, onTabDragLeave, onTabDrop, onTabDragEnd,
  openViewTabMenu, promptDeleteView, cancelDeleteView, confirmDeleteView,
  duplicateView, onViewUpdated,
  saveViewDescription,
  openViewSettings, closeViewSettings,
  addSort, removeSort, onSortSchemaChange, onSortDirectionChange,
} = vm

// ── Query function (uses activeView from viewManager) ─────────────────────────

const isCalendarView = computed(() => activeView.value?.viewType === 'calendar')

/**
 * Calendar views (including the agenda subtype) always receive the full entry
 * set — they render entries across a time axis and cannot work with a
 * paginated subset. The backend never pages them; all other views load a
 * page at a time.
 */
const isUnlimitedView = computed(() =>
  activeView.value?.viewType === 'calendar',
)

/**
 * Load entries of the active view into this instance's result slot.
 *
 * *limit* null loads everything. With *append*, the entries are added below
 * the ones already loaded. Overlapping loads (a search typed quickly) are
 * resolved in the store: only the latest one changes the result.
 */
async function _loadEntries(offset: number, limit: number | null, append: boolean): Promise<void> {
  const view = activeView.value
  if (!view) return
  const result = await dbStore.loadViewPage(
    props.blockId,
    view.id,
    {
      offset,
      limit: isUnlimitedView.value ? null : limit,
      search: nameSearchQuery.value.trim(),
    },
    queryResultKey,
    append,
  )
  if (activeView.value?.id !== view.id) return
  displayedEntries.value = result.entries
  totalEntries.value     = result.total
}

/** Size of the range already loaded, at least one page. */
function _loadedRangeLimit(): number {
  return Math.max(PAGE_SIZE, displayedEntries.value.length)
}

/**
 * Compare the range already loaded with the server's by id: add the entries
 * missing from it, drop the ones the view no longer holds, and put them in
 * the server's order. Values of loaded entries are not fetched here.
 */
async function _syncEntries(): Promise<void> {
  const view = activeView.value
  if (!view) return
  const result = await dbStore.syncViewPage(
    props.blockId,
    view.id,
    {
      offset: 0,
      limit: isUnlimitedView.value ? null : _loadedRangeLimit(),
      search: nameSearchQuery.value.trim(),
    },
    queryResultKey,
  )
  if (activeView.value?.id !== view.id) return
  displayedEntries.value = result.entries
  totalEntries.value     = result.total
}

const _refresh = useCoalescedRefresh((kind) =>
  kind === 'full' ? _loadEntries(0, _loadedRangeLimit(), false) : _syncEntries(),
)

/**
 * Bring the range already loaded up to date after a change to which entries
 * the view holds or to their order: the user's own, or one another client
 * reported. At least one page is covered, so a view that was empty shows a
 * new entry.
 */
function queryFromActiveView(): Promise<void> {
  return _refresh.request('sync')
}

/**
 * Load the range already loaded again in full, values included, when it may
 * be stale in ways no event described: after the connection was down, after
 * an event that did not list the changed entries, or after a property was
 * deleted.
 */
function reloadActiveView(): Promise<void> {
  return _refresh.request('full')
}

/** Start over with the first page, after the view or its filters changed. */
async function resetAndQuery(): Promise<void> {
  await _loadEntries(0, PAGE_SIZE, false)
}

_resetQueryFn = resetAndQuery

/** Load the next *increment* entries and add them below the loaded ones. */
async function loadMore(increment: number): Promise<void> {
  await _loadEntries(displayedEntries.value.length, increment, true)
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

// ── Composable: group-by & aggregations ───────────────────────────────────────

const {
  isGrouped, tableGroups,
  aggPickerKey, getAggregationFn, setAggregation, toggleAggPicker,
} = useDatabaseGrouping({
  activeView,
  schemas,
  entries: filteredAndSortedEntries,
  saveViews,
  editable,
  t,
})

// ── Composable: sub-item tree ─────────────────────────────────────────────────

const {
  parentItemSchema,
  subItemChildrenMap,
  foldedEntries,
  loadFoldState,
  toggleFold,
  flatTreeEntries,
} = useSubItemTree({
  schemas,
  entries: filteredAndSortedEntries,
  isGrouped,
  storageKey: `db-fold-${props.blockId}`,
})

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
  nameColLabel:  computed(() => nameLabel.value),
  t,
})

const { showExportMenu, exportCSV, exportExcel, exportPDF, exportICS } = exp

const EXPORTS: Record<ExportFormat, () => unknown> = {
  csv: exportCSV,
  excel: exportExcel,
  pdf: exportPDF,
  ics: exportICS,
}

function onExport(format: ExportFormat): void {
  EXPORTS[format]()
}

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
  const nameHidden = hidden.has(NAME_COL_KEY)

  for (const id of order) {
    if (id === NAME_COL_KEY) {
      seen.add(NAME_COL_KEY)
      if (!nameHidden) result.push({ key: NAME_COL_KEY, schema: null })
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

  if (!seen.has(NAME_COL_KEY) && !nameHidden) result.unshift({ key: NAME_COL_KEY, schema: null })

  return result
})

// ── Name column visibility & label ────────────────────────────────────────────

/** Whether the active view shows the name column. */
const nameColumnVisible = computed<boolean>(
  () => orderedColumns.value.some(c => c.key === NAME_COL_KEY),
)

/**
 * Key of the column that takes over the name cell's row controls (fold toggle,
 * sub-item indent, entry icon, open button) while the name column is hidden:
 * the first visible column. Null while the name column is shown, or when no
 * column is visible at all, in which case entries open from the row menu.
 */
const leadPrefixKey = computed<string | null>(() =>
  nameColumnVisible.value ? null : (orderedColumns.value[0]?.key ?? null),
)

/** Label of the name column: the database's own, or the localized default. */
const nameLabel = computed<string>(
  () => dbStore.getNameLabel(props.blockId) ?? t('db.nameColumn'),
)

const renamingNameLabel = ref(false)
const nameLabelDraft    = ref('')
// The header sits inside the column loop, where a plain template ref would be
// collected into an array; a function ref keeps the single element.
const nameLabelInputEl  = ref<HTMLInputElement | null>(null)

function setNameLabelInputEl(el: unknown): void {
  nameLabelInputEl.value = el instanceof HTMLInputElement ? el : null
}

async function startNameLabelRename(): Promise<void> {
  if (!editable.value) return
  nameLabelDraft.value    = nameLabel.value
  renamingNameLabel.value = true
  await nextTick()
  nameLabelInputEl.value?.focus()
  nameLabelInputEl.value?.select()
}

function cancelNameLabelRename(): void {
  renamingNameLabel.value = false
}

/**
 * Store the name column's label on the database block. An empty label, or the
 * localized default itself, clears the custom label, so the column follows
 * the interface language again.
 */
async function commitNameLabelRename(): Promise<void> {
  if (!renamingNameLabel.value) return
  renamingNameLabel.value = false
  const trimmed = nameLabelDraft.value.trim()
  const custom  = trimmed && trimmed !== t('db.nameColumn') ? trimmed : null
  if (custom === dbStore.getNameLabel(props.blockId)) return
  const content: Record<string, unknown> = { ...block.value?.content }
  if (custom) content.nameLabel = custom
  else delete content.nameLabel
  await blockStore.updateBlock(props.blockId, { content })
}

// ── Composable: sticky header & frozen columns ────────────────────────────────

const sticky = useStickyColumns({ activeView, activeViewId, orderedColumns })

const {
  theadRowEl, stickyHeaderEnabled,
  handleFrozen, handleFrozenStyle, frozenColStyle, isFrozenCol,
} = sticky

// ── Panel / menu visibility ───────────────────────────────────────────────────

const showAutomationsModal = ref(false)
const showTemplatesModal = ref(false)
/** ID of the entry_template block currently open in the editor, or null. */
const editingTemplateId = ref<string | null>(null)

/**
 * Close every open toolbar panel and menu. Runs on any click outside them
 * (the document listener) and before another toolbar panel opens.
 */
function closeAllPanels() {
  showFilterPanel.value    = false
  showSortPanel.value      = false
  showExportMenu.value     = false
  showTemplatesModal.value = false
  aggPickerKey.value       = null
  viewTabMenuId.value      = null
  clearActiveCell()
  closeRowContextMenu()
}

// ── Bootstrap ─────────────────────────────────────────────────────────────────

/**
 * Whether the active view's rows or their order may depend on *schemaIds*:
 * it filters or sorts by one of them, or a title changed while the name
 * search is in use.
 */
function _viewDependsOn(schemaIds: Set<string>): boolean {
  const view = activeView.value
  if (!view) return true
  if (view.sorts.some((s) => schemaIds.has(s.schemaId))) return true
  if (view.filterGroups.some((g) => g.filters.some((f) => schemaIds.has(f.schemaId)))) return true
  return schemaIds.has(NAME_COL_KEY) && nameSearchQuery.value.trim() !== ''
}

/**
 * Entries of this database changed, here or elsewhere.
 *
 * An event that does not name the changed entries loads the range again in
 * full. Otherwise, when entries were added, removed or reordered, or a change
 * can move a row in or out of the view or change the order, the loaded range
 * is compared with the server's by id. Independently, the loaded entries among
 * the changed ones are fetched and updated in place.
 */
function _applyEntriesEvent(details: EntriesEventDetails): void {
  if (!details.entries) {
    reloadActiveView()
    return
  }
  const changed = new Set(Object.values(details.entries).flat())
  if (details.listing || _viewDependsOn(changed)) queryFromActiveView()
  const loaded = new Set(displayedEntries.value.map((e) => e.id))
  const ids = Object.keys(details.entries).filter((id) => loaded.has(id))
  if (ids.length === 0) return
  dbStore.refreshEntries(props.blockId, ids)
    .then((missing) => { if (missing.length > 0) queryFromActiveView() })
    .catch(() => reloadActiveView())
}

function _onDbEntriesUpdated(e: Event): void {
  const details = entriesEventDetails((e as CustomEvent<BlockEventPayload>).detail)
  if (details && details.database_id === props.blockId) _applyEntriesEvent(details)
}

/** The connection was down: properties, views and rows may be stale. */
async function _onResync(): Promise<void> {
  await Promise.all([
    dbStore.fetchSchemas(props.blockId),
    viewsStore.refreshViews(props.blockId),
  ])
  await reloadActiveView()
}

/**
 * Another client, or a bilateral relation created from another database,
 * changed this database's properties. The backend has already hidden new
 * ones in every view, so the properties and the views are fetched again.
 */
async function _handleRemoteSchemaUpdate(): Promise<void> {
  await Promise.all([
    dbStore.fetchSchemas(props.blockId),
    viewsStore.refreshViews(props.blockId),
  ])
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

onMounted(async () => {
  // The first request for a database's views also creates its system
  // properties, so the properties are fetched once the views are there.
  await Promise.all([
    blockStore.fetchBlock(props.blockId),
    viewsStore.fetchViews(props.blockId, placementId)
      .then(() => dbStore.fetchSchemas(props.blockId)),
  ])

  syncColWidthsFromView(activeView.value)
  loadFoldState()
  await resetAndQuery()
  isLoading.value = false
  document.addEventListener('click', closeAllPanels)
  window.addEventListener(WS_BLOCK_EVENT, _onDbEntriesUpdated)
  window.addEventListener(WS_BLOCK_EVENT, _onDbSchemaUpdated)
  window.addEventListener(WS_RESYNC_EVENT, _onResync)
  sticky.start()

  // From here on, whatever changes the active view – a tab click, a new or
  // deleted view, a view removed by another client – loads its first page.
  // So does a change of its type, since views on a time axis are not paged.
  watch(
    () => [activeView.value?.id, activeView.value?.viewType] as const,
    ([id, type], [previousId, previousType]) => {
      if (!id || (id === previousId && type === previousType)) return
      if (id !== previousId) {
        activeCell.value = null
        syncColWidthsFromView(activeView.value)
      }
      resetAndQuery()
    },
  )
})

onUnmounted(() => {
  document.removeEventListener('click', closeAllPanels)
  window.removeEventListener(WS_BLOCK_EVENT, _onDbEntriesUpdated)
  window.removeEventListener(WS_BLOCK_EVENT, _onDbSchemaUpdated)
  window.removeEventListener(WS_RESYNC_EVENT, _onResync)
  sticky.stop()
  resizeCleanup()
  if (_tipTimer) clearTimeout(_tipTimer)
  if (_nameSearchTimer) clearTimeout(_nameSearchTimer)
  dbStore.releaseQueryResult(queryResultKey)
  dbStore.setEditingCell(queryResultKey, null, null)
})

// ── View switching (orchestrates multiple composables) ────────────────────────

/** Show another view here; the watch on the active view loads it. */
async function switchView(viewId: string): Promise<void> {
  if (activeViewId.value === viewId) return
  activeViewId.value = viewId
  await saveActiveViewId()
}

// ── Add schema ────────────────────────────────────────────────────────────────

const showAddSchema = ref(false)

/**
 * The panel creates the property with the active view as the one it is
 * visible in; the backend hides it in every other view, and the store reloads
 * the views.
 */
function onAddSchemaPanelClose(): void {
  showAddSchema.value = false
}

// ── Property settings modal ───────────────────────────────────────────────────

const settingsSchema = ref<PropertySchema | null>(null)

function openSettings(schema: PropertySchema)  { if (editable.value) settingsSchema.value = schema }
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
  await reloadActiveView()
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
  if (resizingKey.value !== null || !editable.value) { e.preventDefault(); return }
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

// ── Composable: row drag & drop ───────────────────────────────────────────────

const rowDrag = useRowDragDrop({
  databaseId: props.blockId,
  editable,
  parentItemSchema,
  rows: computed(() => flatTreeEntries.value.map(r => r.entry)),
  setParent: async (childId, parentId) => {
    await dbStore.upsertValue(props.blockId, childId, parentItemSchema.value!.id, { related_ids: [parentId] })
  },
  requery: () => queryFromActiveView(),
})

const {
  getRowDropState, clearRowDropState,
  onRowDragStart, onRowDragEnd, onRowDragOver, onRowDrop,
} = rowDrag

// ── Row context menu ──────────────────────────────────────────────────────────

interface RowContextMenu { visible: boolean; x: number; y: number; entryId: string | null }

const rowContextMenu   = ref<RowContextMenu>({ visible: false, x: 0, y: 0, entryId: null })
const rowContextMenuEl = ref<HTMLElement | null>(null)

function openRowContextMenu(e: MouseEvent, entry: DatabaseEntry): void {
  if (rowDrag.isRowDragInProgress()) return
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

function contextMenuOpenEntry(): void {
  const id = rowContextMenu.value.entryId
  closeRowContextMenu()
  if (id) sideViewEntryId.value = id
}

async function contextMenuDuplicateEntry(): Promise<void> {
  const id = rowContextMenu.value.entryId
  closeRowContextMenu()
  if (!id) return
  await dbStore.duplicateEntry(props.blockId, id)
  await queryFromActiveView()
}

// ── Quick Print ───────────────────────────────────────────────────────────────

const { printEntry } = useQuickPrint()

/**
 * Quick Print of the row: the properties of the view's visible columns, in
 * the view's order. The name column is left out; the title heads the print.
 */
function contextMenuQuickPrint(): void {
  const id = rowContextMenu.value.entryId
  closeRowContextMenu()
  hideTip()
  if (!id) return
  const columns = orderedColumns.value.flatMap(c => (c.schema ? [c.schema] : []))
  printEntry(props.blockId, id, columns)
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
    content: { ...block.value?.content, title: trimmed },
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
  if (!editable.value) return
  schemaIconPickerKey.value  = schema.id
  schemaIconPickerRect.value = (event.currentTarget as HTMLElement).getBoundingClientRect()
}

async function onSchemaIconUpdate(schema: PropertySchema, newIcon: string | null): Promise<void> {
  schemaIconPickerKey.value  = null
  schemaIconPickerRect.value = null
  await dbStore.updateSchema(props.blockId, schema.id, {
    config: { ...schema.config, icon: newIcon ?? undefined },
  })
}

// ── Entry icon picker ─────────────────────────────────────────────────────────

/** ID of the entry whose icon picker is currently open, or null. */
const entryIconPickerEntryId = ref<string | null>(null)
const entryIconPickerRect    = ref<DOMRect | null>(null)

function openEntryIconPicker(entry: DatabaseEntry, event: MouseEvent): void {
  event.stopPropagation()
  if (!editable.value) return
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
  // immediately without waiting for the change event.
  const row = displayedEntries.value.find(e => e.id === id)
  if (row) row.icon = newIcon ?? null
}

/**
 * Resolve the display icon for a row entry.
 * Prefer blockStore.blocks so that a freshly-set icon appears instantly even
 * before the change event updates displayedEntries.
 */
function entryIcon(entry: DatabaseEntry): string {
  return blockStore.blocks[entry.id]?.icon ?? entry.icon ?? 'mdi:file-outline'
}

// ── Name cell editing ─────────────────────────────────────────────────────────

const editingNameId = ref<string | null>(null)
const nameDraft     = ref('')

/** Click on an entry's name: edit it, or open the entry without write access. */
function startNameEdit(entry: DatabaseEntry) {
  if (!editable.value) {
    openEntry(entry)
    return
  }
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
    content: { ...entry.content, title: trimmed },
  })
  await queryFromActiveView()
}

// ── Side view ─────────────────────────────────────────────────────────────────

const sideViewEntryId = ref<string | null>(null)

function openEntry(entry: DatabaseEntry) { sideViewEntryId.value = entry.id }
function closeSideView(): void           { sideViewEntryId.value = null }

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

// Someone else changing the cell being edited here is reported to the user.
watch(activeCell, (cell) => {
  dbStore.setEditingCell(queryResultKey, cell?.entryId ?? null, cell?.schemaId ?? null)
})

function onCellClick(entry: DatabaseEntry, schema: PropertySchema): void {
  if (!editable.value) return
  if (isReadonlyPropertyType(schema.type)) return
  if (schema.type === 'checkbox' || schema.type === 'relation' || schema.type === 'file') return
  if (schema.type === 'select' && (schema.config?.mode ?? 'single') === 'multiple') return
  setActiveCell(entry.id, schema.id)
}

// ── Relation change ───────────────────────────────────────────────────────────

/**
 * Write a relation, parent or sub-item value.
 *
 * The cell shows the new value at once, and the tree of sub-items is built
 * from the ``parent_item`` values of the loaded rows, so nothing has to be
 * loaded here. What the server changed besides, mirror values on other
 * entries and the formulas and rollups reading them, arrives as
 * ``database_entries_updated`` naming those entries, which every view of the
 * affected databases answers by loading just them (``_applyEntriesEvent``).
 * A mirror property the write created is announced as
 * ``database_schema_updated`` first.
 */
async function handleRelationChange(
  entry: DatabaseEntry,
  schema: PropertySchema,
  value: Record<string, unknown> | null,
): Promise<void> {
  await dbStore.upsertValue(props.blockId, entry.id, schema.id, value)
}

// ── Composable: add row ───────────────────────────────────────────────────────

// Column types whose cell opens an inline editor when activated, and can
// therefore take the focus of a freshly created row.
const INLINE_EDIT_TYPES = new Set(['text', 'number', 'select', 'date', 'email', 'phone', 'url'])

function isInlineEditable(schema: PropertySchema): boolean {
  if (!INLINE_EDIT_TYPES.has(schema.type)) return false
  if (schema.type === 'select' && schema.config?.mode === 'multiple') return false
  // Timelined cells open the timeline editor instead of an inline field.
  return !schema.config?.hasTimeline
}

/**
 * Put the cursor into a freshly created row.
 *
 * With the name column shown, its field opens, pre-filled with a name derived
 * from the view's filters. With the name column hidden, a derived name is
 * saved directly and the first visible column that edits inline is activated
 * instead; without one, the row is only scrolled into view.
 */
async function focusNewEntry(entryId: string, derivedName: string | null): Promise<void> {
  if (nameColumnVisible.value) {
    editingNameId.value = entryId
    nameDraft.value     = derivedName ?? ''
    await nextTick()
    const input = document.querySelector<HTMLElement>(`[data-entry-id="${entryId}"] .db__cell-input`)
    input?.scrollIntoView({ block: 'nearest' })
    input?.focus()
    return
  }

  if (derivedName) {
    await blockStore.updateBlock(entryId, { content: { title: derivedName } })
  }
  const target = orderedColumns.value.find(c => c.schema !== null && isInlineEditable(c.schema))
  if (target?.schema) setActiveCell(entryId, target.schema.id)
  await nextTick()
  document.querySelector<HTMLElement>(`[data-entry-id="${entryId}"]`)?.scrollIntoView({ block: 'nearest' })
}

const { isAddingRow, addRow, addGroupRow, addRowOnDate } = useAddRow({
  databaseId: props.blockId,
  activeView,
  schemas,
  editable,
  createEntry: () => dbStore.createEntry(props.blockId),
  writeValue: (entryId, schemaId, value) => dbStore.upsertValue(props.blockId, entryId, schemaId, value),
  requery: () => queryFromActiveView(),
  onCreated: focusNewEntry,
})

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
            :class="{ 'db__title-icon-btn--static': !editable }"
            :disabled="!editable"
            :title="editable ? t('main.addIcon') : undefined"
            @click.stop="showIconPicker = !showIconPicker"
          >
            <Icon :icon="block?.icon ?? 'mdi:table-large'" class="db__title-icon" width="28" height="28" />
          </button>
          <IconPicker
            v-if="showIconPicker && editable"
            :model-value="block?.icon ?? null"
            @update:model-value="onIconUpdate"
            @close="showIconPicker = false"
          />
        </div>
        <input
          class="db__title-input"
          :value="block?.content?.title ?? ''"
          :placeholder="t('main.untitled')"
          :readonly="!editable"
          @vue:mounted="onTitleMount"
          @input="titleDraft = ($event.target as HTMLInputElement).value"
          @blur="saveTitle"
        />
      </div>

      <!-- ── View tabs ──────────────────────────────────────────────────── -->
      <DatabaseViewTabs
        v-model:name-draft="viewNameDraft"
        :views="views"
        :active-view-id="activeViewId"
        :renaming-view-id="renamingViewId"
        :tab-drag-id="tabDragId"
        :tab-drop-id="tabDropId"
        @switch="switchView"
        @start-rename="startRenameView"
        @commit-rename="commitRenameView"
        @cancel-rename="cancelRenameView"
        @open-menu="openViewTabMenu"
        @add="addView"
        @tab-drag-start="onTabDragStart"
        @tab-drag-over="onTabDragOver"
        @tab-drag-leave="onTabDragLeave"
        @tab-drop="onTabDrop"
        @tab-drag-end="onTabDragEnd"
      />

      <!-- ── Toolbar ────────────────────────────────────────────────────── -->
      <DatabaseToolbar
        v-model:show-filter-panel="showFilterPanel"
        v-model:show-sort-panel="showSortPanel"
        v-model:show-export-menu="showExportMenu"
        v-model:show-templates-modal="showTemplatesModal"
        v-model:show-automations-modal="showAutomationsModal"
        v-model:search-query="nameSearchQuery"
        :database-id="blockId"
        :inline="inline"
        :filter-count="filterCount"
        :sort-count="sortCount"
        :is-calendar-view="isCalendarView"
        :show-new-entry="!activeView || activeView.viewType === 'table'"
        :has-more="hasMore"
        :is-adding-row="isAddingRow"
        @open-view-settings="openViewSettings(activeViewId, $event)"
        @add-row="addRow"
        @export="onExport"
        @edit-template="editingTemplateId = $event"
        @show-tip="showTip"
        @hide-tip="hideTip"
      >
        <template #filter-panel>
          <FilterPanel
            :active-view="activeView"
            :schemas="schemas"
            :displayed-entries="displayedEntries"
            :name-col-key="NAME_COL_KEY"
            :name-label="nameLabel"
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
        </template>
        <template #sort-panel>
          <SortPanel
            :active-view="activeView"
            :schemas="schemas"
            :name-col-key="NAME_COL_KEY"
            :name-label="nameLabel"
            @add-sort="addSort"
            @remove-sort="removeSort"
            @sort-schema-change="onSortSchemaChange"
            @sort-direction-change="onSortDirectionChange"
          />
        </template>
      </DatabaseToolbar>

      <!-- ── View description ───────────────────────────────────────────── -->
      <!--
        Keyed by view so switching tabs never carries an open edit over. The
        component reports the id of the view it was rendered for, so a blur
        caused by clicking another tab still saves into the edited view.
      -->
      <ViewDescription
        v-if="activeView"
        :key="activeView.id"
        :view-id="activeView.id"
        :description="activeView.description ?? ''"
        :readonly="!editable"
        @save="saveViewDescription"
      />

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
                    :draggable="editable && !renamingNameLabel"
                    @dragstart="onColDragStart($event, NAME_COL_KEY)"
                    @dragover="onColDragOver($event, NAME_COL_KEY)"
                    @dragleave="onColDragLeave(NAME_COL_KEY)"
                    @drop="onColDrop($event, NAME_COL_KEY)"
                    @dragend="onColDragEnd"
                  >
                    <div class="db__th-inner">
                      <input
                        v-if="renamingNameLabel"
                        :ref="setNameLabelInputEl"
                        v-model="nameLabelDraft"
                        class="db__th-rename-input"
                        :placeholder="t('db.nameColumn')"
                        :aria-label="t('db.nameColumnRename')"
                        @blur="commitNameLabelRename"
                        @keydown.enter.prevent="commitNameLabelRename"
                        @keydown.escape.prevent="cancelNameLabelRename"
                        @click.stop
                      />
                      <span v-else class="db__th-label">
                        <span
                          class="db__th-name"
                          @dblclick.stop="hideTip(); startNameLabelRename()"
                          @mouseenter="editable && showTip($event, t('db.nameColumnRenameHint'))"
                          @mouseleave="hideTip"
                        >{{ nameLabel }}</span>
                      </span>
                      <button
                        v-if="editable"
                        class="db__th-btn"
                        :class="{ 'db__th-btn--wrap-active': isWrapped(NAME_COL_KEY) }"
                        :title="isWrapped(NAME_COL_KEY) ? t('db.wrapColumnOff') : t('db.wrapColumnOn')"
                        @click.stop="toggleWrapColumn(NAME_COL_KEY)"
                      >
                        <Icon icon="mdi:wrap" width="13" height="13" />
                      </button>
                    </div>
                    <span
                      v-if="editable"
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
                    :draggable="editable"
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
                            :class="{ 'db__th-icon-btn--static': !editable }"
                            :title="editable ? t('db.changeIcon') : undefined"
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
                          :class="{ 'db__th-name--static': !editable }"
                          @click="openSettings(col.schema!)"
                          @mouseenter="showColumnTip($event, col.schema!)"
                          @mouseleave="hideTip"
                        >{{ col.schema!.name }}</span>
                      </span>
                      <template v-if="!editable" />
                      <template v-else-if="deletingSchemaId === col.key">
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
                        v-if="editable && deletingSchemaId !== col.key && supportsLineWrap(col.schema)"
                        class="db__th-btn"
                        :class="{ 'db__th-btn--wrap-active': isWrapped(col.key) }"
                        :title="isWrapped(col.key) ? t('db.wrapColumnOff') : t('db.wrapColumnOn')"
                        @click.stop="toggleWrapColumn(col.key)"
                      >
                        <Icon icon="mdi:wrap" width="13" height="13" />
                      </button>
                    </div>
                    <span
                      v-if="editable"
                      class="db__th-resize"
                      :class="{ 'db__th-resize--active': resizingKey === col.key }"
                      @pointerdown.stop.prevent="(e) => startResize(e, col.key, (e.currentTarget as HTMLElement).closest('th') as HTMLElement)"
                    />
                  </th>
                </template>
                <th class="db__th db__th--add">
                  <button v-if="editable" class="db__add-col-btn" :title="t('db.addSchema.title')" @click="showAddSchema = true">
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
                  <span v-if="editable" class="db__row-handle" draggable="true" @click.stop="openRowContextMenu($event, entry)">
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
                      @vue:mounted="(vnode: VNode) => (vnode.el as HTMLInputElement | null)?.focus()"
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
                        :title="foldedEntries.has(entry.id) ? t('db.expandSubItems') : t('db.collapseSubItems')"
                        @click.stop="toggleFold(entry.id)"
                      >
                        <Icon icon="mdi:chevron-down" width="12" height="12" />
                      </button>
                      <!-- Placeholder keeps alignment when no fold button is shown -->
                      <span v-else class="db__fold-placeholder" aria-hidden="true" />
                      <div class="db__entry-icon-wrap">
                        <button
                          class="db__entry-icon-btn"
                          :class="{ 'db__entry-icon-btn--static': !editable }"
                          :title="editable ? t('main.addIcon') : undefined"
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
                  <td v-else class="db__td" :class="{ 'db__td--wrap': isWrapped(col.key), 'db__td--frozen': isFrozenCol(col.key), 'db__td--pending': dbStore.isCellPending(entry.id, col.schema!.id) }" :style="frozenColStyle(col.key)" :aria-busy="dbStore.isCellPending(entry.id, col.schema!.id) || undefined" @click="onCellClick(entry, col.schema!)">
                    <!--
                      While the name column is hidden, the first visible column carries the
                      row controls the name cell would otherwise hold.
                    -->
                    <div class="db__cell-wrap" :class="{ 'db__cell-wrap--lead': col.key === leadPrefixKey }">
                      <div v-if="col.key === leadPrefixKey" class="db__lead-prefix" @click.stop>
                        <!-- Sub-item depth indent and fold toggle, as in the name cell -->
                        <span
                          v-if="depth > 0"
                          class="db__sub-indent"
                          :style="{ width: (depth * 20) + 'px' }"
                          aria-hidden="true"
                        />
                        <button
                          v-if="subItemChildrenMap.has(entry.id)"
                          class="db__fold-btn"
                          :class="{ 'db__fold-btn--folded': foldedEntries.has(entry.id) }"
                          :title="foldedEntries.has(entry.id) ? t('db.expandSubItems') : t('db.collapseSubItems')"
                          @click.stop="toggleFold(entry.id)"
                        >
                          <Icon icon="mdi:chevron-down" width="12" height="12" />
                        </button>
                        <span v-else class="db__fold-placeholder" aria-hidden="true" />
                        <div class="db__entry-icon-wrap">
                          <button
                            class="db__entry-icon-btn"
                            :class="{ 'db__entry-icon-btn--static': !editable }"
                            :title="editable ? t('main.addIcon') : undefined"
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
                        <button class="db__open-btn" :title="t('db.openEntry')" @click.stop="openEntry(entry)">
                          <Icon icon="mdi:arrow-top-right" width="12" height="12" />
                        </button>
                      </div>
                      <div class="db__cell-body">
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
                      </div>
                    </div>
                    <div
                      v-if="dbStore.isCellPending(entry.id, col.schema!.id)"
                      class="db__cell-pending"
                      role="status"
                      :title="t('db.cellSaving')"
                      :aria-label="t('db.cellSaving')"
                      @click.stop
                    >
                      <Icon icon="mdi:loading" class="db__spinner" width="13" height="13" />
                    </div>
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
                  <button v-if="editable" class="db__group-add-btn" :disabled="isAddingRow" @mouseenter="hasMore ? showTip($event, t('db.addRowLimitHint')) : undefined" @mouseleave="hideTip" @click="addRow">
                    <Icon icon="mdi:plus" width="13" height="13" />
                    {{ t('db.addRowShort') }}
                  </button>
                </td>
                <template v-for="col in orderedColumns" :key="col.key">
                  <td
                    class="db__aggregation-cell"
                    :class="{ 'db__aggregation-cell--frozen': isFrozenCol(col.key) }"
                    :style="{ ...colStyle(col.key), ...frozenColStyle(col.key) }"
                    @click.stop="toggleAggPicker('flat:' + col.key)"
                  >
                    <div class="db__agg-inner">
                      <template v-if="getAggregationFn(col.key) !== 'none'">
                        <span class="db__agg-badge">{{ aggregationBadge(getAggregationFn(col.key)) }}</span>
                        <span class="db__agg-value">{{ computeAggregation(filteredAndSortedEntries, col.key, col.schema, getAggregationFn(col.key)) }}</span>
                      </template>
                      <span v-else-if="editable" class="db__agg-plus"><Icon icon="mdi:plus" width="11" height="11" /></span>
                      <div v-if="aggPickerKey === ('flat:' + col.key)" class="db__agg-picker" @click.stop>
                        <button
                          v-for="opt in aggregationOptions(col.schema)"
                          :key="opt"
                          class="db__agg-picker-opt"
                          :class="{ 'db__agg-picker-opt--active': getAggregationFn(col.key) === opt }"
                          @click="setAggregation(col.key, opt)"
                        >
                          {{ opt === 'none' ? t('db.viewSettings.aggNone') : aggregationBadge(opt) }}
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
                    <span v-if="editable" class="db__row-handle" draggable="true" @click.stop="openRowContextMenu($event, entry)">
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
                        @vue:mounted="(vnode: VNode) => (vnode.el as HTMLInputElement | null)?.focus()"
                      />
                      <div v-else class="db__name-cell">
                        <div class="db__entry-icon-wrap">
                          <button
                            class="db__entry-icon-btn"
                            :class="{ 'db__entry-icon-btn--static': !editable }"
                            :title="editable ? t('main.addIcon') : undefined"
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
                    <td v-else class="db__td" :class="{ 'db__td--wrap': isWrapped(col.key), 'db__td--frozen': isFrozenCol(col.key), 'db__td--pending': dbStore.isCellPending(entry.id, col.schema!.id) }" :style="frozenColStyle(col.key)" :aria-busy="dbStore.isCellPending(entry.id, col.schema!.id) || undefined" @click="onCellClick(entry, col.schema!)">
                      <!--
                        While the name column is hidden, the first visible column carries the
                        row controls the name cell would otherwise hold.
                      -->
                      <div class="db__cell-wrap" :class="{ 'db__cell-wrap--lead': col.key === leadPrefixKey }">
                        <div v-if="col.key === leadPrefixKey" class="db__lead-prefix" @click.stop>
                          <div class="db__entry-icon-wrap">
                            <button
                              class="db__entry-icon-btn"
                              :class="{ 'db__entry-icon-btn--static': !editable }"
                              :title="editable ? t('main.addIcon') : undefined"
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
                          <button class="db__open-btn" :title="t('db.openEntry')" @click.stop="openEntry(entry)">
                            <Icon icon="mdi:arrow-top-right" width="12" height="12" />
                          </button>
                        </div>
                        <div class="db__cell-body">
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
                        </div>
                      </div>
                      <div
                        v-if="dbStore.isCellPending(entry.id, col.schema!.id)"
                        class="db__cell-pending"
                        role="status"
                        :title="t('db.cellSaving')"
                        :aria-label="t('db.cellSaving')"
                        @click.stop
                      >
                        <Icon icon="mdi:loading" class="db__spinner" width="13" height="13" />
                      </div>
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
                    <button v-if="editable" class="db__group-add-btn" :disabled="isAddingRow" @mouseenter="hasMore ? showTip($event, t('db.addRowLimitHint')) : undefined" @mouseleave="hideTip" @click="addGroupRow(group.groupValue)">
                      <Icon icon="mdi:plus" width="13" height="13" />
                      {{ t('db.addRowShort') }}
                    </button>
                  </td>
                  <template v-for="col in orderedColumns" :key="col.key">
                    <td
                      class="db__aggregation-cell"
                      :class="{ 'db__aggregation-cell--frozen': isFrozenCol(col.key) }"
                      :style="{ ...colStyle(col.key), ...frozenColStyle(col.key) }"
                      @click.stop="toggleAggPicker(group.key + ':' + col.key)"
                    >
                      <div class="db__agg-inner">
                        <template v-if="getAggregationFn(col.key) !== 'none'">
                          <span class="db__agg-badge">{{ aggregationBadge(getAggregationFn(col.key)) }}</span>
                          <span class="db__agg-value">{{ computeAggregation(group.entries, col.key, col.schema, getAggregationFn(col.key)) }}</span>
                        </template>
                        <span v-else-if="editable" class="db__agg-plus"><Icon icon="mdi:plus" width="11" height="11" /></span>
                        <div v-if="aggPickerKey === (group.key + ':' + col.key)" class="db__agg-picker" @click.stop>
                          <button
                            v-for="opt in aggregationOptions(col.schema)"
                            :key="opt"
                            class="db__agg-picker-opt"
                            :class="{ 'db__agg-picker-opt--active': getAggregationFn(col.key) === opt }"
                            @click="setAggregation(col.key, opt)"
                          >
                            {{ opt === 'none' ? t('db.viewSettings.aggNone') : aggregationBadge(opt) }}
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
        <button class="db__row-ctx-item" @click="contextMenuOpenEntry">
          <Icon icon="mdi:arrow-top-right" width="13" height="13" />
          {{ t('db.openEntry') }}
        </button>
        <button v-if="editable" class="db__row-ctx-item" @click="contextMenuDuplicateEntry">
          <Icon icon="mdi:content-copy" width="13" height="13" />
          {{ t('actions.duplicate') }}
        </button>
        <button
          class="db__row-ctx-item"
          @mouseenter="showTip($event, t('quickPrint.tooltip'))"
          @mouseleave="hideTip"
          @click="contextMenuQuickPrint"
        >
          <Icon icon="mdi:printer-outline" width="13" height="13" />
          {{ t('quickPrint.label') }}
        </button>
        <button v-if="editable" class="db__row-ctx-item db__row-ctx-item--danger" @click="contextMenuDeleteEntry">
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
      :visible-in-view-id="activeViewId || undefined"
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

/*
 * Pinned cells must stay opaque, or the scrolled cells show through them.
 * The drop tint is translucent (an accent mix), so on pinned cells it is
 * painted as a layer over the opaque canvas colour.
 */
.db__row--drop-onto > td.db__td--frozen {
  background:
    linear-gradient(
      color-mix(in srgb, var(--color-accent) 6%, transparent),
      color-mix(in srgb, var(--color-accent) 6%, transparent)
    ),
    var(--color-bg);
}

/* ── Lead prefix (row controls while the name column is hidden) ──────────── */
/*
 * Every data cell's content sits in .db__cell-wrap > .db__cell-body. Both
 * are layout-neutral (display: contents) so cell components lay out exactly
 * as if they were direct children of the <td>. Only in the first visible
 * column, while the name column is hidden, does the wrapper become a flex row
 * that puts the row controls in front of the cell.
 */
.db__cell-wrap,
.db__cell-body {
  display: contents;
}

.db__cell-wrap--lead {
  display: flex;
  align-items: center;
  min-width: 0;
}

.db__cell-wrap--lead > .db__cell-body {
  display: block;
  flex: 1;
  min-width: 0;
}

.db__lead-prefix {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
  padding-left: 8px;
}

.db__cell-wrap--lead:hover .db__open-btn { opacity: 1; }

/* ── Read-only controls ──────────────────────────────────────────────────── */
.db__title-icon-btn--static,
.db__title-icon-btn--static:hover,
.db__th-icon-btn--static,
.db__th-icon-btn--static:hover,
.db__entry-icon-btn--static,
.db__entry-icon-btn--static:hover {
  cursor: default;
  background: transparent;
}

.db__th-name--static {
  cursor: default;
}

/* ── Name column header rename ───────────────────────────────────────────── */
.db__th-rename-input {
  flex: 1;
  min-width: 0;
  background: transparent;
  border: none;
  outline: 1px solid var(--color-accent);
  border-radius: 2px;
  font: inherit;
  color: var(--color-text);
  padding: 0 3px;
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

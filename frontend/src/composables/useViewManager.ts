/**
 * useViewManager
 *
 * Owns all database-view state: the views array, active view, rename flow,
 * tab drag-reorder, tab action menu, view settings modal, and sort management.
 *
 * Caller (DatabaseBlock.vue) is responsible for orchestrating cross-composable
 * side-effects such as syncing column widths and re-querying entries after a
 * view switch.
 */
import { ref, computed, nextTick, type ComputedRef } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBlockStore } from '@/stores/blocks'
import type { PropertySchema, DatabaseView, ViewType } from '@/stores/database'

// ── View-type icon map (exported so template can reference it) ────────────────

export const VIEW_TYPE_ICONS: Record<string, string> = {
  table:       'mdi:table-large',
  calendar:    'mdi:calendar-month-outline',
  list:        'mdi:format-list-bulleted',
  gallery:     'mdi:view-grid-outline',
  board:       'mdi:view-column-outline',
  family_tree: 'mdi:file-tree-outline',
  mindmap:     'mdi:graph-outline',
  graph:       'mdi:graph',
}

export function viewTypeIcon(type?: string): string {
  return VIEW_TYPE_ICONS[type ?? 'table'] ?? 'mdi:table-large'
}

// ── Composable ────────────────────────────────────────────────────────────────

export function useViewManager(options: {
  blockId: string
  schemas: ComputedRef<PropertySchema[]>
  NAME_COL_KEY: string
  PREF_VIEWS: string
  PREF_ACTIVE_VIEW: string
  READONLY_SCHEMA_TYPES: Set<string>
  /** Called after any sort mutation that requires a backend re-query. */
  onQueryNeeded: () => Promise<void>
}) {
  const {
    blockId, schemas, NAME_COL_KEY, PREF_VIEWS, PREF_ACTIVE_VIEW,
    READONLY_SCHEMA_TYPES, onQueryNeeded,
  } = options

  const { t } = useI18n()
  const blockStore = useBlockStore()

  // ── Core state ──────────────────────────────────────────────────────────────

  const views         = ref<DatabaseView[]>([])
  const activeViewId  = ref<string>('')
  const showSortPanel = ref(false)

  // ── Rename ──────────────────────────────────────────────────────────────────

  const renamingViewId = ref<string | null>(null)
  const viewNameDraft  = ref('')

  // ── Tab drag-reorder ────────────────────────────────────────────────────────

  const tabDragId = ref<string | null>(null)
  const tabDropId = ref<string | null>(null)

  // ── Tab action menu ─────────────────────────────────────────────────────────

  const viewTabMenuId  = ref<string | null>(null)
  const viewTabMenuPos = ref<{ x: number; y: number }>({ x: 0, y: 0 })

  // ── Delete confirmation ─────────────────────────────────────────────────────

  const deleteConfirmViewId = ref<string | null>(null)

  // ── View settings modal ─────────────────────────────────────────────────────

  const viewSettingsViewId = ref<string | null>(null)

  // ── Computed ────────────────────────────────────────────────────────────────

  const activeView = computed<DatabaseView | null>(
    () => views.value.find((v) => v.id === activeViewId.value) ?? views.value[0] ?? null,
  )

  const viewSettingsView = computed<DatabaseView | null>(
    () => views.value.find((v) => v.id === viewSettingsViewId.value) ?? null,
  )

  const sortCount = computed(() => activeView.value?.sorts.length ?? 0)

  // ── Persistence ─────────────────────────────────────────────────────────────

  async function saveViews(): Promise<void> {
    await blockStore.setPreference(blockId, PREF_VIEWS, [...views.value])
  }

  async function saveActiveViewId(): Promise<void> {
    await blockStore.setPreference(blockId, PREF_ACTIVE_VIEW, activeViewId.value)
  }

  // ── View factory ────────────────────────────────────────────────────────────

  function buildDefaultView(
    colWidths: Record<string, number> = {},
    viewType: ViewType = 'table',
  ): DatabaseView {
    const hiddenColumns = schemas.value
      .filter(s => READONLY_SCHEMA_TYPES.has(s.type))
      .map(s => s.id)
    return {
      id: crypto.randomUUID(),
      name: t('db.views.defaultName'),
      viewType,
      colOrder: [NAME_COL_KEY, ...schemas.value.map((s) => s.id)],
      colWidths,
      filterGroups: [],
      sorts: [],
      hiddenColumns,
    }
  }

  // ── CRUD ────────────────────────────────────────────────────────────────────

  async function addView(): Promise<void> {
    const newView = buildDefaultView()
    newView.name = `${t('db.views.defaultName').replace(/\s\d+$/, '')} ${views.value.length + 1}`
    views.value.push(newView)
    activeViewId.value = newView.id
    await saveViews()
    await saveActiveViewId()
    startRenameView(newView.id)
  }

  async function deleteView(viewId: string): Promise<void> {
    if (views.value.length <= 1) return
    const idx = views.value.findIndex((v) => v.id === viewId)
    if (idx === -1) return
    views.value.splice(idx, 1)
    if (activeViewId.value === viewId) {
      activeViewId.value = views.value[Math.max(0, idx - 1)].id
    }
    await saveViews()
    await saveActiveViewId()
  }

  // ── Rename ──────────────────────────────────────────────────────────────────

  async function startRenameView(viewId: string): Promise<void> {
    const view = views.value.find((v) => v.id === viewId)
    if (!view) return
    renamingViewId.value = viewId
    viewNameDraft.value  = view.name
    await nextTick()
    const el = document.querySelector<HTMLInputElement>('.db__tab-rename-input')
    el?.focus()
    el?.select()
  }

  async function commitRenameView(viewId: string): Promise<void> {
    if (renamingViewId.value !== viewId) return
    const view = views.value.find((v) => v.id === viewId)
    if (view) {
      const trimmed = viewNameDraft.value.trim()
      view.name = trimmed || view.name
    }
    renamingViewId.value = null
    await saveViews()
  }

  function cancelRenameView(): void {
    renamingViewId.value = null
  }

  // ── Tab drag-reorder ────────────────────────────────────────────────────────

  function onTabDragStart(e: DragEvent, viewId: string): void {
    if (renamingViewId.value !== null) { e.preventDefault(); return }
    tabDragId.value = viewId
    if (e.dataTransfer) {
      e.dataTransfer.effectAllowed = 'move'
      e.dataTransfer.setData('text/plain', viewId)
    }
  }

  function onTabDragOver(e: DragEvent, viewId: string): void {
    if (!tabDragId.value || tabDragId.value === viewId) return
    e.preventDefault()
    tabDropId.value = viewId
  }

  function onTabDragLeave(viewId: string): void {
    if (tabDropId.value === viewId) tabDropId.value = null
  }

  function onTabDrop(e: DragEvent, targetId: string): void {
    e.preventDefault()
    tabDropId.value = null
    const sourceId = tabDragId.value
    tabDragId.value = null
    if (!sourceId || sourceId === targetId) return
    const fromIdx = views.value.findIndex((v) => v.id === sourceId)
    const toIdx   = views.value.findIndex((v) => v.id === targetId)
    if (fromIdx === -1 || toIdx === -1) return
    const [moved] = views.value.splice(fromIdx, 1)
    views.value.splice(toIdx, 0, moved)
    saveViews()
  }

  function onTabDragEnd(): void {
    tabDragId.value = null
    tabDropId.value = null
  }

  // ── Tab action menu ─────────────────────────────────────────────────────────

  function openViewTabMenu(viewId: string, e: MouseEvent): void {
    e.stopPropagation()
    if (viewTabMenuId.value === viewId) { viewTabMenuId.value = null; return }
    const btn  = e.currentTarget as HTMLElement
    const rect = btn.getBoundingClientRect()
    viewTabMenuPos.value = { x: rect.left, y: rect.bottom + 4 }
    viewTabMenuId.value  = viewId
  }

  function promptDeleteView(viewId: string): void {
    viewTabMenuId.value       = null
    deleteConfirmViewId.value = viewId
  }

  function cancelDeleteView(): void {
    deleteConfirmViewId.value = null
  }

  async function confirmDeleteView(): Promise<void> {
    const id = deleteConfirmViewId.value
    deleteConfirmViewId.value = null
    if (id) await deleteView(id)
  }

  async function duplicateView(viewId: string): Promise<void> {
    viewTabMenuId.value = null
    const source = views.value.find((v) => v.id === viewId)
    if (!source) return
    const copy: DatabaseView = JSON.parse(JSON.stringify(source))
    copy.id   = crypto.randomUUID()
    copy.name = `${source.name} (2)`
    const idx = views.value.findIndex((v) => v.id === viewId)
    views.value.splice(idx + 1, 0, copy)
    await saveViews()
  }

  function onViewUpdated(updated: DatabaseView): void {
    const idx = views.value.findIndex(v => v.id === updated.id)
    if (idx !== -1) views.value[idx] = updated
    saveViews()
  }

  // ── View settings modal ─────────────────────────────────────────────────────

  function openViewSettings(viewId: string, e: Event): void {
    e.stopPropagation()
    viewSettingsViewId.value = viewId
  }

  function closeViewSettings(): void {
    viewSettingsViewId.value = null
  }

  // ── Sort management ─────────────────────────────────────────────────────────

  async function addSort(): Promise<void> {
    const view = activeView.value
    if (!view) return
    const firstSchemaId = schemas.value[0]?.id ?? NAME_COL_KEY
    view.sorts.push({ id: crypto.randomUUID(), schemaId: firstSchemaId, direction: 'asc' })
    await saveViews()
    await onQueryNeeded()
  }

  async function removeSort(sortId: string): Promise<void> {
    const view = activeView.value
    if (!view) return
    view.sorts = view.sorts.filter((s) => s.id !== sortId)
    await saveViews()
    await onQueryNeeded()
  }

  async function onSortSchemaChange(sortId: string, newSchemaId: string): Promise<void> {
    const view = activeView.value
    if (!view) return
    const s = view.sorts.find((s) => s.id === sortId)
    if (!s) return
    s.schemaId = newSchemaId
    await saveViews()
    await onQueryNeeded()
  }

  async function onSortDirectionChange(sortId: string, newDirection: 'asc' | 'desc'): Promise<void> {
    const view = activeView.value
    if (!view) return
    const s = view.sorts.find((s) => s.id === sortId)
    if (!s) return
    s.direction = newDirection
    await saveViews()
    await onQueryNeeded()
  }

  // ── Public API ──────────────────────────────────────────────────────────────

  return {
    // State
    views,
    activeViewId,
    activeView,
    renamingViewId,
    viewNameDraft,
    tabDragId,
    tabDropId,
    viewTabMenuId,
    viewTabMenuPos,
    deleteConfirmViewId,
    viewSettingsViewId,
    viewSettingsView,
    showSortPanel,
    sortCount,
    // Persistence
    saveViews,
    saveActiveViewId,
    // View factory
    buildDefaultView,
    // CRUD
    addView,
    deleteView,
    // Rename
    startRenameView,
    commitRenameView,
    cancelRenameView,
    // Tab drag
    onTabDragStart,
    onTabDragOver,
    onTabDragLeave,
    onTabDrop,
    onTabDragEnd,
    // Tab action menu
    openViewTabMenu,
    promptDeleteView,
    cancelDeleteView,
    confirmDeleteView,
    duplicateView,
    onViewUpdated,
    // View settings
    openViewSettings,
    closeViewSettings,
    // Sort
    addSort,
    removeSort,
    onSortSchemaChange,
    onSortDirectionChange,
  }
}

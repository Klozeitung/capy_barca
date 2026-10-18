/**
 * useViewManager
 *
 * UI state and actions around the views of one database placement: which
 * view is active, the rename flow, tab drag-reorder, the tab action menu, the
 * view description, the view settings modal, and sort management.
 *
 * The views themselves live in the databaseViews store, one list per
 * database shared by every block that shows it. This composable changes them
 * in place and asks the store to save what changed; the store sends only the
 * changed fields. Which view is active is the choice of this placement (the
 * database block itself or a linked block) and is saved per placement.
 *
 * Caller (DatabaseBlock.vue) is responsible for orchestrating cross-composable
 * side-effects such as syncing column widths and reloading entries after a
 * view switch.
 */
import { ref, computed, nextTick, type ComputedRef } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDatabaseViewsStore } from '@/stores/databaseViews'
import {
  normalizeViewDescription,
  type PropertySchema,
  type DatabaseView,
} from '@/stores/database'

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

/**
 * The name shown for a view. A view that was never named has an empty name
 * in storage and shows the localized default.
 */
export function viewDisplayName(
  view: Pick<DatabaseView, 'name'> | null | undefined,
  t: (key: string) => string,
): string {
  return view?.name || t('db.views.defaultName')
}

// ── Composable ────────────────────────────────────────────────────────────────

export function useViewManager(options: {
  /** The database whose views are shown. */
  databaseId: string
  /** The block showing them: the database itself or a linked block. */
  placementId: string
  schemas: ComputedRef<PropertySchema[]>
  NAME_COL_KEY: string
  /** Called after any sort mutation that requires a backend re-query. */
  onQueryNeeded: () => Promise<void>
}) {
  const { databaseId, placementId, schemas, NAME_COL_KEY, onQueryNeeded } = options

  const { t } = useI18n()
  const viewsStore = useDatabaseViewsStore()

  // ── Core state ──────────────────────────────────────────────────────────────

  const views = computed<DatabaseView[]>(() => viewsStore.getViews(databaseId))

  /**
   * The active view of this placement. Writing it changes the choice locally
   * only; ``saveActiveViewId`` stores it.
   */
  const activeViewId = computed<string>({
    get: () => viewsStore.getSelectedViewId(databaseId, placementId) ?? '',
    set: (id: string) => { viewsStore.selections[placementId] = id },
  })

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

  /** Save whatever changed in any view of this database. */
  async function saveViews(): Promise<void> {
    await viewsStore.persistViews(databaseId)
  }

  /** Save whatever changed in one view. */
  async function saveView(viewId: string): Promise<void> {
    await viewsStore.persistView(databaseId, viewId)
  }

  /** Store which view this placement shows. */
  async function saveActiveViewId(): Promise<void> {
    if (!activeViewId.value) return
    await viewsStore.selectView(databaseId, placementId, activeViewId.value)
  }

  // ── CRUD ────────────────────────────────────────────────────────────────────

  async function addView(): Promise<void> {
    const base = t('db.views.defaultName').replace(/\s\d+$/, '')
    const created = await viewsStore.createView(databaseId, {
      name: `${base} ${views.value.length + 1}`,
    })
    activeViewId.value = created.id
    await saveActiveViewId()
    startRenameView(created.id)
  }

  async function deleteView(viewId: string): Promise<void> {
    if (views.value.length <= 1) return
    const idx = views.value.findIndex((v) => v.id === viewId)
    if (idx === -1) return
    const wasActive = activeViewId.value === viewId
    const fallback = views.value[idx === 0 ? 1 : idx - 1].id
    await viewsStore.deleteView(databaseId, viewId)
    if (wasActive) {
      activeViewId.value = fallback
      await saveActiveViewId()
    }
  }

  // ── Rename ──────────────────────────────────────────────────────────────────

  async function startRenameView(viewId: string): Promise<void> {
    const view = views.value.find((v) => v.id === viewId)
    if (!view) return
    renamingViewId.value = viewId
    viewNameDraft.value  = viewDisplayName(view, t)
    await nextTick()
    const el = document.querySelector<HTMLInputElement>('.db__tab-rename-input')
    el?.focus()
    el?.select()
  }

  async function commitRenameView(viewId: string): Promise<void> {
    if (renamingViewId.value !== viewId) return
    renamingViewId.value = null
    const view = views.value.find((v) => v.id === viewId)
    if (!view) return
    const trimmed = viewNameDraft.value.trim()
    // Keeping the shown default name does not turn it into a stored one.
    if (!trimmed || trimmed === viewDisplayName(view, t)) return
    view.name = trimmed
    await saveView(viewId)
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

  async function onTabDrop(e: DragEvent, targetId: string): Promise<void> {
    e.preventDefault()
    tabDropId.value = null
    const sourceId = tabDragId.value
    tabDragId.value = null
    if (!sourceId || sourceId === targetId) return
    const order   = views.value.map((v) => v.id)
    const fromIdx = order.indexOf(sourceId)
    const toIdx   = order.indexOf(targetId)
    if (fromIdx === -1 || toIdx === -1) return
    order.splice(fromIdx, 1)
    order.splice(toIdx, 0, sourceId)
    await viewsStore.reorderViews(databaseId, order)
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
    // Pending edits of the source belong in the copy.
    await saveView(viewId)
    await viewsStore.duplicateView(databaseId, viewId, `${viewDisplayName(source, t)} (2)`)
  }

  /**
   * Store the description of the view *viewId*.
   *
   * The view is addressed by id rather than through ``activeView`` because the
   * edit is committed on blur, and a click on another view tab blurs the field
   * before the tab switch runs. An empty description removes the field, so a
   * view without one carries nothing into storage or the exports.
   */
  async function saveViewDescription(viewId: string, raw: string): Promise<void> {
    const view = views.value.find((v) => v.id === viewId)
    if (!view) return
    const next = normalizeViewDescription(raw)
    if (next === (view.description ?? '')) return
    if (next) view.description = next
    else delete view.description
    await saveView(viewId)
  }

  /**
   * Take over a changed copy of a view, as the settings modal and the
   * calendar views emit it, and save the difference.
   */
  async function onViewUpdated(updated: DatabaseView): Promise<void> {
    const view = views.value.find((v) => v.id === updated.id)
    if (!view) return
    for (const key of Object.keys(view) as (keyof DatabaseView)[]) {
      if (!(key in updated)) delete view[key]
    }
    Object.assign(view, updated)
    await saveView(view.id)
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

  async function _saveActiveAndQuery(): Promise<void> {
    const view = activeView.value
    if (!view) return
    await saveView(view.id)
    await onQueryNeeded()
  }

  async function addSort(): Promise<void> {
    const view = activeView.value
    if (!view) return
    const firstSchemaId = schemas.value[0]?.id ?? NAME_COL_KEY
    view.sorts.push({ id: crypto.randomUUID(), schemaId: firstSchemaId, direction: 'asc' })
    await _saveActiveAndQuery()
  }

  async function removeSort(sortId: string): Promise<void> {
    const view = activeView.value
    if (!view) return
    view.sorts = view.sorts.filter((s) => s.id !== sortId)
    await _saveActiveAndQuery()
  }

  async function onSortSchemaChange(sortId: string, newSchemaId: string): Promise<void> {
    const view = activeView.value
    if (!view) return
    const s = view.sorts.find((s) => s.id === sortId)
    if (!s) return
    s.schemaId = newSchemaId
    await _saveActiveAndQuery()
  }

  async function onSortDirectionChange(sortId: string, newDirection: 'asc' | 'desc'): Promise<void> {
    const view = activeView.value
    if (!view) return
    const s = view.sorts.find((s) => s.id === sortId)
    if (!s) return
    s.direction = newDirection
    await _saveActiveAndQuery()
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
    saveView,
    saveActiveViewId,
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
    // Description
    saveViewDescription,
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

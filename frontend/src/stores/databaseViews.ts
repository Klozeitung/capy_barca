/**
 * databaseViews store
 *
 * Holds the views of every database that is on screen, and which view each
 * placement shows. Backed by the /api/databases/{id}/views endpoints, where
 * the backend owns, validates and executes views.
 *
 * One copy per database
 * ---------------------
 * Views are shared by every user and every placement of a database, so the
 * store keeps exactly one list per database. Two blocks showing the same
 * database on one page edit the same objects and see each other's changes at
 * once. Which view a block shows is a separate, per-placement choice
 * (``selections``): a placement is the database block itself or a
 * ``linked_database`` block pointing at it.
 *
 * Shapes
 * ------
 * Components work with the flat ``DatabaseView`` shape from the database
 * store. The API splits a view into ``name``, ``view_type``, ``description``,
 * ``filter_groups``, ``sorts`` and a ``settings`` object holding the
 * presentation keys. ``fromApi`` and ``toParts`` translate between the two.
 *
 * Saving
 * ------
 * Callers change a view in place and then call ``persistView``. The store
 * remembers the server state of every view and sends only what differs from
 * it: the changed fields, and within ``settings`` the changed keys, with
 * ``null`` for a key that was removed. Two people adjusting different aspects
 * of the same view therefore do not overwrite each other. Saves of one view
 * run one after another, so each compares against the state the previous one
 * left. Fields that changed on the server meanwhile and were not touched
 * locally are taken over from the response.
 */
import { ref } from 'vue'
import { defineStore } from 'pinia'
import { apiClient } from '@/api/client'
import type { DatabaseView, ViewType } from '@/stores/database'

// ── API shapes ────────────────────────────────────────────────────────────────

export interface ApiView {
  id: string
  database_id: string
  name: string
  view_type: string
  position: number
  description: string | null
  filter_groups: DatabaseView['filterGroups']
  sorts: DatabaseView['sorts']
  settings: Record<string, unknown>
}

interface ApiViewList {
  views: ApiView[]
  selected_view_id: string | null
}

/** A view as the API stores it, without identity and position. */
export interface ViewParts {
  name: string
  view_type: string
  description: string | null
  filter_groups: DatabaseView['filterGroups']
  sorts: DatabaseView['sorts']
  settings: Record<string, unknown>
}

type PartKey = Exclude<keyof ViewParts, 'settings'>

const PART_KEYS: PartKey[] = ['name', 'view_type', 'description', 'filter_groups', 'sorts']

/** Keys of ``DatabaseView`` that are not presentation settings. */
const NON_SETTINGS_KEYS = new Set([
  'id', 'name', 'viewType', 'description', 'filterGroups', 'sorts', 'filters',
])

// ── Translation ───────────────────────────────────────────────────────────────

/** The flat frontend view for an API view. */
export function fromApi(api: ApiView): DatabaseView {
  const settings = api.settings ?? {}
  const view: DatabaseView = {
    ...(settings as Partial<DatabaseView>),
    id: api.id,
    name: api.name ?? '',
    viewType: (api.view_type || 'table') as ViewType,
    filterGroups: api.filter_groups ?? [],
    sorts: api.sorts ?? [],
    colOrder: (settings.colOrder as string[] | undefined) ?? [],
    colWidths: (settings.colWidths as Record<string, number> | undefined) ?? {},
    hiddenColumns: (settings.hiddenColumns as string[] | undefined) ?? [],
  }
  if (api.description) view.description = api.description
  return view
}

/** The API parts of a flat frontend view. */
export function toParts(view: DatabaseView): ViewParts {
  const settings: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(view)) {
    if (NON_SETTINGS_KEYS.has(key) || value === undefined || value === null) continue
    settings[key] = value
  }
  return {
    name: view.name ?? '',
    view_type: view.viewType ?? 'table',
    description: view.description ? view.description : null,
    filter_groups: view.filterGroups ?? [],
    sorts: view.sorts ?? [],
    settings,
  }
}

function same(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b)
}

/**
 * A detached copy of *parts*. Remembered server states must not share arrays
 * or objects with the views, which callers change in place.
 */
function detached(parts: ViewParts): ViewParts {
  return JSON.parse(JSON.stringify(parts)) as ViewParts
}

/**
 * The PATCH body that turns *base* into *current*: changed fields, and the
 * changed settings keys with ``null`` for removed ones. Null when nothing
 * changed.
 */
export function diffParts(base: ViewParts, current: ViewParts): Record<string, unknown> | null {
  const patch: Record<string, unknown> = {}
  for (const key of PART_KEYS) {
    if (!same(base[key], current[key])) patch[key] = current[key]
  }
  const settings: Record<string, unknown> = {}
  const keys = new Set([...Object.keys(base.settings), ...Object.keys(current.settings)])
  for (const key of keys) {
    if (!(key in current.settings)) settings[key] = null
    else if (!same(base.settings[key], current.settings[key])) settings[key] = current.settings[key]
  }
  if (Object.keys(settings).length > 0) patch.settings = settings
  return Object.keys(patch).length > 0 ? patch : null
}

/** *base* with *patch* applied, as the server applies it. */
export function applyPatch(base: ViewParts, patch: Record<string, unknown>): ViewParts {
  const result: ViewParts = { ...base, settings: { ...base.settings } }
  for (const key of PART_KEYS) {
    if (key in patch) (result as unknown as Record<string, unknown>)[key] = patch[key]
  }
  const settings = (patch.settings as Record<string, unknown> | undefined) ?? {}
  for (const [key, value] of Object.entries(settings)) {
    if (value === null) delete result.settings[key]
    else result.settings[key] = value
  }
  return result
}

/**
 * Take over into *view* what the server changed between *before* and *after*,
 * for every field and settings key the local copy still holds as *before*.
 * Local edits made while a save was in flight are kept.
 */
function adoptServerChanges(
  view: DatabaseView,
  before: ViewParts,
  after: ViewParts,
): void {
  const local = toParts(view)
  const merged: ViewParts = { ...local, settings: { ...local.settings } }
  for (const key of PART_KEYS) {
    if (same(local[key], before[key]) && !same(before[key], after[key])) {
      ;(merged as unknown as Record<string, unknown>)[key] = after[key]
    }
  }
  const keys = new Set([...Object.keys(before.settings), ...Object.keys(after.settings)])
  for (const key of keys) {
    if (!same(local.settings[key], before.settings[key])) continue
    if (same(before.settings[key], after.settings[key])) continue
    if (key in after.settings) merged.settings[key] = after.settings[key]
    else delete merged.settings[key]
  }
  if (same(merged, local)) return
  const next = fromApi({
    ...merged,
    id: view.id,
    database_id: '',
    position: 0,
  })
  for (const key of Object.keys(view) as (keyof DatabaseView)[]) {
    if (!(key in next)) delete view[key]
  }
  Object.assign(view, next)
}

// ── Store ─────────────────────────────────────────────────────────────────────

export const useDatabaseViewsStore = defineStore('databaseViews', () => {
  /** Views per database id, in tab order. */
  const views = ref<Record<string, DatabaseView[]>>({})

  /** Selected view id per placement id. */
  const selections = ref<Record<string, string>>({})

  /** Last known server state per view id. */
  const serverState = new Map<string, ViewParts>()

  /** Pending save per view id, so saves of one view never overlap. */
  const saveQueue = new Map<string, Promise<void>>()

  function _remember(api: ApiView): DatabaseView {
    const view = fromApi(api)
    serverState.set(view.id, detached(toParts(view)))
    return view
  }

  function _forget(viewId: string): void {
    serverState.delete(viewId)
    saveQueue.delete(viewId)
  }

  function getViews(databaseId: string): DatabaseView[] {
    return views.value[databaseId] ?? []
  }

  function isLoaded(databaseId: string): boolean {
    return databaseId in views.value
  }

  /**
   * The view *placementId* shows: its stored choice, or the first view when
   * it has none or the chosen view is gone.
   */
  function getSelectedViewId(databaseId: string, placementId: string): string | null {
    const list = getViews(databaseId)
    const chosen = selections.value[placementId]
    if (chosen && list.some((v) => v.id === chosen)) return chosen
    return list[0]?.id ?? null
  }

  // ── Loading ─────────────────────────────────────────────────────────────────

  /**
   * Load the views of *databaseId* and the choice of *placementId*.
   *
   * The first request for a database also makes the backend create its
   * system properties and a default view, so callers fetch schemas after
   * this.
   */
  async function fetchViews(databaseId: string, placementId?: string): Promise<DatabaseView[]> {
    const query = placementId && placementId !== databaseId
      ? `?placement_id=${encodeURIComponent(placementId)}`
      : ''
    const result = await apiClient.get<ApiViewList>(`/api/databases/${databaseId}/views${query}`)
    for (const old of getViews(databaseId)) {
      if (!result.views.some((v) => v.id === old.id)) _forget(old.id)
    }
    views.value[databaseId] = result.views.map(_remember)
    if (result.selected_view_id) {
      selections.value[placementId ?? databaseId] = result.selected_view_id
    }
    return views.value[databaseId]
  }

  /**
   * Reload the views of *databaseId* if they are on screen. Used after the
   * backend changed them as a side effect, for instance when a property was
   * added or deleted.
   */
  async function refreshViews(databaseId: string): Promise<void> {
    if (!isLoaded(databaseId)) return
    await fetchViews(databaseId)
  }

  // ── Mutations ───────────────────────────────────────────────────────────────

  function _insertByPosition(databaseId: string, view: DatabaseView, afterId?: string): void {
    const list = getViews(databaseId).slice()
    const index = afterId ? list.findIndex((v) => v.id === afterId) : -1
    if (index === -1) list.push(view)
    else list.splice(index + 1, 0, view)
    views.value[databaseId] = list
  }

  /** Append a new view with the default layout. */
  async function createView(
    databaseId: string,
    payload: { name: string; viewType?: ViewType },
  ): Promise<DatabaseView> {
    const created = await apiClient.post<ApiView>(`/api/databases/${databaseId}/views`, {
      name: payload.name,
      view_type: payload.viewType ?? 'table',
    })
    const view = _remember(created)
    _insertByPosition(databaseId, view)
    return view
  }

  /** Copy a view; the copy is placed right after the original. */
  async function duplicateView(
    databaseId: string,
    viewId: string,
    name: string,
  ): Promise<DatabaseView> {
    await _drain(viewId)
    const created = await apiClient.post<ApiView>(
      `/api/databases/${databaseId}/views/${viewId}/duplicate`,
      { name },
    )
    const view = _remember(created)
    _insertByPosition(databaseId, view, viewId)
    return view
  }

  /** Delete a view. Placements that showed it fall back to the first view. */
  async function deleteView(databaseId: string, viewId: string): Promise<void> {
    await apiClient.delete(`/api/databases/${databaseId}/views/${viewId}`)
    views.value[databaseId] = getViews(databaseId).filter((v) => v.id !== viewId)
    _forget(viewId)
    for (const [placement, chosen] of Object.entries(selections.value)) {
      if (chosen === viewId) delete selections.value[placement]
    }
  }

  /** Put the views of *databaseId* into the given order. */
  async function reorderViews(databaseId: string, viewIds: string[]): Promise<void> {
    const byId = new Map(getViews(databaseId).map((v) => [v.id, v]))
    views.value[databaseId] = viewIds
      .map((id) => byId.get(id))
      .filter((v): v is DatabaseView => v !== undefined)
    await apiClient.put(`/api/databases/${databaseId}/views/order`, { view_ids: viewIds })
  }

  /** Remember that *placementId* shows *viewId*. */
  async function selectView(databaseId: string, placementId: string, viewId: string): Promise<void> {
    selections.value[placementId] = viewId
    await apiClient.put(`/api/databases/${databaseId}/views/selection`, {
      placement_id: placementId,
      view_id: viewId,
    })
  }

  async function _drain(viewId: string): Promise<void> {
    const pending = saveQueue.get(viewId)
    if (pending) await pending.catch(() => undefined)
  }

  /**
   * Save what changed in view *viewId* since its last known server state.
   * Does nothing when nothing changed.
   */
  function persistView(databaseId: string, viewId: string): Promise<void> {
    const previous = saveQueue.get(viewId) ?? Promise.resolve()
    const next = previous
      .catch(() => undefined)
      .then(async () => {
        const view = getViews(databaseId).find((v) => v.id === viewId)
        const base = serverState.get(viewId)
        if (!view || !base) return
        const patch = diffParts(base, toParts(view))
        if (!patch) return
        const updated = await apiClient.patch<ApiView>(
          `/api/databases/${databaseId}/views/${viewId}`,
          patch,
        )
        const after = detached(toParts(fromApi(updated)))
        serverState.set(viewId, after)
        adoptServerChanges(view, applyPatch(base, patch), detached(after))
      })
    saveQueue.set(viewId, next)
    return next.finally(() => {
      if (saveQueue.get(viewId) === next) saveQueue.delete(viewId)
    })
  }

  /** Save every changed view of *databaseId*. */
  async function persistViews(databaseId: string): Promise<void> {
    await Promise.all(getViews(databaseId).map((v) => persistView(databaseId, v.id)))
  }

  return {
    // State
    views,
    selections,

    // Reading
    getViews,
    isLoaded,
    getSelectedViewId,

    // Loading
    fetchViews,
    refreshViews,

    // Mutations
    createView,
    duplicateView,
    deleteView,
    reorderViews,
    selectView,
    persistView,
    persistViews,
  }
})

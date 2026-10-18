import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}))

import { apiClient } from '@/api/client'
import {
  useDatabaseViewsStore,
  fromApi,
  toParts,
  diffParts,
  applyPatch,
  type ApiView,
} from '@/stores/databaseViews'

function apiView(id: string, over: Partial<ApiView> = {}): ApiView {
  return {
    id,
    database_id: 'db-1',
    name: 'View A',
    view_type: 'table',
    position: 1,
    description: null,
    filter_groups: [],
    sorts: [],
    settings: { colOrder: ['__name__'], colWidths: {}, hiddenColumns: [] },
    ...over,
  }
}

async function loaded(...views: ApiView[]) {
  vi.mocked(apiClient.get).mockResolvedValueOnce({
    views,
    selected_view_id: views[0]?.id ?? null,
  })
  const store = useDatabaseViewsStore()
  await store.fetchViews('db-1')
  vi.clearAllMocks()
  return store
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

// ── Translation ───────────────────────────────────────────────────────────────

describe('fromApi / toParts', () => {
  it('flattens the settings into the view and splits them back', () => {
    const api = apiView('v1', {
      description: 'Text',
      view_type: 'calendar',
      settings: {
        colOrder: ['__name__', 's1'], colWidths: { s1: 120 }, hiddenColumns: ['s2'],
        calendarDateSchemaId: 's1', frozenColumns: 1,
      },
    })

    const view = fromApi(api)

    expect(view.viewType).toBe('calendar')
    expect(view.calendarDateSchemaId).toBe('s1')
    expect(view.description).toBe('Text')
    expect(toParts(view)).toEqual({
      name: 'View A',
      view_type: 'calendar',
      description: 'Text',
      filter_groups: [],
      sorts: [],
      settings: api.settings,
    })
  })

  it('fills the column lists a view always has', () => {
    const view = fromApi(apiView('v1', { settings: {} }))
    expect(view.colOrder).toEqual([])
    expect(view.colWidths).toEqual({})
    expect(view.hiddenColumns).toEqual([])
    expect('description' in view).toBe(false)
  })

  it('leaves out settings that are unset', () => {
    const view = fromApi(apiView('v1'))
    view.groupBySchemaId = undefined
    expect('groupBySchemaId' in toParts(view).settings).toBe(false)
  })
})

describe('diffParts', () => {
  const base = toParts(fromApi(apiView('v1', {
    settings: { colOrder: ['__name__'], colWidths: {}, hiddenColumns: [], groupBySchemaId: 's1' },
  })))

  it('is null when nothing changed', () => {
    expect(diffParts(base, structuredClone(base))).toBeNull()
  })

  it('names only the changed fields and settings keys', () => {
    const current = structuredClone(base)
    current.name = 'Renamed'
    current.settings.colWidths = { s1: 90 }
    expect(diffParts(base, current)).toEqual({
      name: 'Renamed',
      settings: { colWidths: { s1: 90 } },
    })
  })

  it('sends null for a removed settings key', () => {
    const current = structuredClone(base)
    delete current.settings.groupBySchemaId
    expect(diffParts(base, current)).toEqual({ settings: { groupBySchemaId: null } })
  })

  it('applyPatch reproduces the result the server stores', () => {
    const current = structuredClone(base)
    current.sorts = [{ id: 'o1', schemaId: 's1', direction: 'asc' }]
    delete current.settings.groupBySchemaId
    expect(applyPatch(base, diffParts(base, current)!)).toEqual(current)
  })
})

// ── Store ─────────────────────────────────────────────────────────────────────

describe('useDatabaseViewsStore', () => {
  it('loads the views and the selection of a placement', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      views: [apiView('v1'), apiView('v2')], selected_view_id: 'v2',
    })
    const store = useDatabaseViewsStore()

    await store.fetchViews('db-1', 'linked-1')

    expect(apiClient.get).toHaveBeenCalledWith('/api/databases/db-1/views?placement_id=linked-1')
    expect(store.getSelectedViewId('db-1', 'linked-1')).toBe('v2')
    expect(store.getSelectedViewId('db-1', 'db-1')).toBe('v1')
  })

  it('persistView sends nothing when nothing changed', async () => {
    const store = await loaded(apiView('v1'))
    await store.persistView('db-1', 'v1')
    expect(apiClient.patch).not.toHaveBeenCalled()
  })

  it('persistView sends only the difference', async () => {
    const store = await loaded(apiView('v1'))
    vi.mocked(apiClient.patch).mockResolvedValueOnce(apiView('v1', {
      settings: { colOrder: ['__name__'], colWidths: { s1: 90 }, hiddenColumns: [] },
    }))
    store.getViews('db-1')[0].colWidths = { s1: 90 }

    await store.persistView('db-1', 'v1')
    await store.persistView('db-1', 'v1')

    expect(apiClient.patch).toHaveBeenCalledTimes(1)
    expect(apiClient.patch).toHaveBeenCalledWith(
      '/api/databases/db-1/views/v1',
      { settings: { colWidths: { s1: 90 } } },
    )
  })

  it('takes over what someone else changed in fields left untouched', async () => {
    const store = await loaded(apiView('v1'))
    // The response carries another client's sort next to our name.
    vi.mocked(apiClient.patch).mockResolvedValueOnce(apiView('v1', {
      name: 'Renamed',
      sorts: [{ id: 'o1', schemaId: 's1', direction: 'desc' }],
    }))
    store.getViews('db-1')[0].name = 'Renamed'

    await store.persistView('db-1', 'v1')

    expect(store.getViews('db-1')[0].sorts).toEqual([
      { id: 'o1', schemaId: 's1', direction: 'desc' },
    ])
  })

  it('keeps a local edit made while a save was in flight and saves it next', async () => {
    const store = await loaded(apiView('v1'))
    let respond: (v: ApiView) => void = () => {}
    vi.mocked(apiClient.patch)
      .mockImplementationOnce(() => new Promise((r) => { respond = r as typeof respond }))
      .mockResolvedValueOnce(apiView('v1', { name: 'First', description: 'Later' }))
    const view = store.getViews('db-1')[0]

    view.name = 'First'
    const first = store.persistView('db-1', 'v1')
    await vi.waitFor(() => expect(apiClient.patch).toHaveBeenCalledTimes(1))
    view.description = 'Later'
    const second = store.persistView('db-1', 'v1')
    respond(apiView('v1', { name: 'First' }))
    await Promise.all([first, second])

    expect(view.description).toBe('Later')
    expect(vi.mocked(apiClient.patch).mock.calls.map((c) => c[1])).toEqual([
      { name: 'First' },
      { description: 'Later' },
    ])
  })

  it('refreshViews reloads only databases that are on screen', async () => {
    const store = useDatabaseViewsStore()
    await store.refreshViews('db-1')
    expect(apiClient.get).not.toHaveBeenCalled()

    await loaded(apiView('v1'))
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      views: [apiView('v1', { settings: { hiddenColumns: ['s9'] } })], selected_view_id: 'v1',
    })
    await store.refreshViews('db-1')

    expect(store.getViews('db-1')[0].hiddenColumns).toEqual(['s9'])
  })

  it('deleting a view drops the selections that showed it', async () => {
    const store = await loaded(apiView('v1'), apiView('v2'))
    await store.selectView('db-1', 'linked-1', 'v2')

    await store.deleteView('db-1', 'v2')

    expect(store.getViews('db-1').map((v) => v.id)).toEqual(['v1'])
    expect(store.getSelectedViewId('db-1', 'linked-1')).toBe('v1')
  })

  it('reorders locally and on the server', async () => {
    const store = await loaded(apiView('v1'), apiView('v2'))

    await store.reorderViews('db-1', ['v2', 'v1'])

    expect(store.getViews('db-1').map((v) => v.id)).toEqual(['v2', 'v1'])
    expect(apiClient.put).toHaveBeenCalledWith(
      '/api/databases/db-1/views/order', { view_ids: ['v2', 'v1'] },
    )
  })

  it('places a duplicate right after its source', async () => {
    const store = await loaded(apiView('v1'), apiView('v2'))
    vi.mocked(apiClient.post).mockResolvedValueOnce(apiView('v3'))

    await store.duplicateView('db-1', 'v1', 'Copy')

    expect(store.getViews('db-1').map((v) => v.id)).toEqual(['v1', 'v3', 'v2'])
  })
})

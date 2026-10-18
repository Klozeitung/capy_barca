import { describe, it, expect, beforeEach, vi } from 'vitest'
import { computed } from 'vue'
import { setActivePinia, createPinia } from 'pinia'

// The composable only needs ``t`` for default view names.
vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => (key === 'db.views.defaultName' ? 'View 1' : key) }),
}))

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
import { useDatabaseViewsStore, type ApiView } from '@/stores/databaseViews'
import { useViewManager, viewDisplayName } from '@/composables/useViewManager'

function apiView(id: string, over: Partial<ApiView> = {}): ApiView {
  return {
    id,
    database_id: 'db-1',
    name: id,
    view_type: 'table',
    position: 1,
    description: null,
    filter_groups: [],
    sorts: [],
    settings: { colOrder: ['__name__'], colWidths: {}, hiddenColumns: [] },
    ...over,
  }
}

/** A manager for placement *placementId* over two loaded views. */
async function manager(placementId = 'db-1') {
  vi.mocked(apiClient.get).mockResolvedValueOnce({
    views: [apiView('v1'), apiView('v2', { description: 'Second', position: 2 })],
    selected_view_id: 'v1',
  })
  await useDatabaseViewsStore().fetchViews('db-1', placementId)
  vi.clearAllMocks()
  return useViewManager({
    databaseId: 'db-1',
    placementId,
    schemas: computed(() => []),
    NAME_COL_KEY: '__name__',
    onQueryNeeded: async () => {},
  })
}

function patchedWith(viewId: string) {
  return vi.mocked(apiClient.patch).mock.calls.find(
    ([url]) => url === `/api/databases/db-1/views/${viewId}`,
  )?.[1] as Record<string, unknown> | undefined
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.mocked(apiClient.patch).mockImplementation(async (url, body) => {
    const id = String(url).split('/').pop()!
    const store = useDatabaseViewsStore()
    const view = store.getViews('db-1').find((v) => v.id === id)!
    return {
      ...apiView(id),
      name: view.name,
      description: view.description ?? null,
      ...(body as object),
    }
  })
})

// ── viewDisplayName ───────────────────────────────────────────────────────────

describe('viewDisplayName', () => {
  const t = (key: string) => (key === 'db.views.defaultName' ? 'View 1' : key)

  it('shows the stored name', () => {
    expect(viewDisplayName({ name: 'Mine' }, t)).toBe('Mine')
  })

  it('shows the localized default for an unnamed view', () => {
    expect(viewDisplayName({ name: '' }, t)).toBe('View 1')
  })
})

// ── Active view per placement ─────────────────────────────────────────────────

describe('useViewManager – active view', () => {
  it('starts with the view the backend selected for the placement', async () => {
    const vm = await manager()
    expect(vm.activeView.value?.id).toBe('v1')
  })

  it('stores a switch for this placement only', async () => {
    const vm = await manager('linked-1')
    vm.activeViewId.value = 'v2'
    await vm.saveActiveViewId()

    expect(apiClient.put).toHaveBeenCalledWith(
      '/api/databases/db-1/views/selection',
      { placement_id: 'linked-1', view_id: 'v2' },
    )
    expect(useDatabaseViewsStore().getSelectedViewId('db-1', 'db-1')).toBe('v1')
  })
})

// ── saveViewDescription ───────────────────────────────────────────────────────

describe('useViewManager – saveViewDescription', () => {
  it('stores a normalised description on the addressed view and saves only it', async () => {
    const vm = await manager()
    await vm.saveViewDescription('v1', '  First line\r\nSecond line  ')
    expect(vm.views.value[0].description).toBe('First line\nSecond line')
    expect(apiClient.patch).toHaveBeenCalledTimes(1)
    expect(patchedWith('v1')).toEqual({ description: 'First line\nSecond line' })
  })

  it('saves into the addressed view even when another view is active', async () => {
    // A blur caused by clicking another tab commits after the switch was
    // requested; the edit must still land on the view it belongs to.
    const vm = await manager()
    vm.activeViewId.value = 'v2'
    await vm.saveViewDescription('v1', 'For the first view')
    expect(vm.views.value[0].description).toBe('For the first view')
    expect(vm.views.value[1].description).toBe('Second')
  })

  it('clears the description when it is emptied', async () => {
    const vm = await manager()
    await vm.saveViewDescription('v2', '   ')
    expect('description' in vm.views.value[1]).toBe(false)
    expect(patchedWith('v2')).toEqual({ description: null })
  })

  it('does not save when nothing changed', async () => {
    const vm = await manager()
    await vm.saveViewDescription('v2', '  Second ')
    await vm.saveViewDescription('v1', '')
    expect(apiClient.patch).not.toHaveBeenCalled()
  })

  it('ignores an unknown view', async () => {
    const vm = await manager()
    await vm.saveViewDescription('missing', 'Text')
    expect(apiClient.patch).not.toHaveBeenCalled()
  })
})

// ── Views CRUD ────────────────────────────────────────────────────────────────

describe('useViewManager – views', () => {
  it('duplicates through the backend and names the copy', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce(apiView('v3', { name: 'v2 (2)' }))
    const vm = await manager()

    await vm.duplicateView('v2')

    expect(apiClient.post).toHaveBeenCalledWith(
      '/api/databases/db-1/views/v2/duplicate', { name: 'v2 (2)' },
    )
    expect(vm.views.value.map((v) => v.id)).toEqual(['v1', 'v2', 'v3'])
  })

  it('names the copy of an unnamed view after the default name', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce(apiView('v3'))
    const vm = await manager()
    vm.views.value[0].name = ''
    await vm.saveView('v1')

    await vm.duplicateView('v1')

    expect(vi.mocked(apiClient.post).mock.calls[0][1]).toEqual({ name: 'View 1 (2)' })
  })

  it('adds a view, selects it for this placement', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce(apiView('v3', { name: 'View 3' }))
    const vm = await manager()

    await vm.addView()

    expect(apiClient.post).toHaveBeenCalledWith(
      '/api/databases/db-1/views', { name: 'View 3', view_type: 'table' },
    )
    expect(vm.activeViewId.value).toBe('v3')
  })

  it('deleting the active view selects its neighbour', async () => {
    const vm = await manager()
    vm.activeViewId.value = 'v2'

    await vm.deleteView('v2')

    expect(apiClient.delete).toHaveBeenCalledWith('/api/databases/db-1/views/v2')
    expect(vm.activeViewId.value).toBe('v1')
    expect(vm.views.value.map((v) => v.id)).toEqual(['v1'])
  })

  it('never deletes the last view', async () => {
    const vm = await manager()
    await vm.deleteView('v2')
    vi.clearAllMocks()

    await vm.deleteView('v1')

    expect(apiClient.delete).not.toHaveBeenCalled()
  })

  it('keeping the shown default name on rename stores nothing', async () => {
    const vm = await manager()
    vm.views.value[0].name = ''
    await vm.saveView('v1')
    vi.clearAllMocks()

    await vm.startRenameView('v1')
    expect(vm.viewNameDraft.value).toBe('View 1')
    await vm.commitRenameView('v1')

    expect(apiClient.patch).not.toHaveBeenCalled()
    expect(vm.views.value[0].name).toBe('')
  })

  it('a rename saves the name only', async () => {
    const vm = await manager()
    await vm.startRenameView('v1')
    vm.viewNameDraft.value = '  Renamed  '
    await vm.commitRenameView('v1')

    expect(patchedWith('v1')).toEqual({ name: 'Renamed' })
  })

  it('a sort change saves the sorts of the active view', async () => {
    const onQueryNeeded = vi.fn(async () => {})
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      views: [apiView('v1')], selected_view_id: 'v1',
    })
    await useDatabaseViewsStore().fetchViews('db-1')
    vi.clearAllMocks()
    const vm = useViewManager({
      databaseId: 'db-1',
      placementId: 'db-1',
      schemas: computed(() => []),
      NAME_COL_KEY: '__name__',
      onQueryNeeded,
    })

    await vm.addSort()

    const body = patchedWith('v1')
    expect(Object.keys(body ?? {})).toEqual(['sorts'])
    expect(onQueryNeeded).toHaveBeenCalledTimes(1)
  })
})

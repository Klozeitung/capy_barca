import { describe, it, expect, beforeEach, vi } from 'vitest'
import { computed } from 'vue'
import { setActivePinia, createPinia } from 'pinia'
import type { DatabaseView } from '@/stores/database'

// The composable only needs ``t`` for default view names.
vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key }),
}))

const setPreference = vi.fn()
vi.mock('@/stores/blocks', () => ({
  useBlockStore: () => ({ setPreference }),
}))

import { useViewManager } from '@/composables/useViewManager'

function view(id: string, over: Partial<DatabaseView> = {}): DatabaseView {
  return {
    id,
    name: id,
    viewType: 'table',
    colOrder: [],
    colWidths: {},
    filterGroups: [],
    sorts: [],
    hiddenColumns: [],
    ...over,
  }
}

function manager() {
  const vm = useViewManager({
    blockId: 'db-1',
    schemas: computed(() => []),
    NAME_COL_KEY: '__name__',
    PREF_VIEWS: 'views',
    PREF_ACTIVE_VIEW: 'active_view',
    READONLY_SCHEMA_TYPES: new Set<string>(),
    onQueryNeeded: async () => {},
  })
  vm.views.value = [view('v1'), view('v2', { description: 'Second' })]
  vm.activeViewId.value = 'v1'
  return vm
}

// ── saveViewDescription ───────────────────────────────────────────────────────

describe('useViewManager – saveViewDescription', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    setPreference.mockReset()
  })

  it('stores a normalised description on the addressed view and persists', async () => {
    const vm = manager()
    await vm.saveViewDescription('v1', '  First line\r\nSecond line  ')
    expect(vm.views.value[0].description).toBe('First line\nSecond line')
    expect(setPreference).toHaveBeenCalledTimes(1)
    expect(setPreference.mock.calls[0][1]).toBe('views')
  })

  it('saves into the addressed view even when another view is active', async () => {
    // A blur caused by clicking another tab commits after the switch was
    // requested; the edit must still land on the view it belongs to.
    const vm = manager()
    vm.activeViewId.value = 'v2'
    await vm.saveViewDescription('v1', 'For the first view')
    expect(vm.views.value[0].description).toBe('For the first view')
    expect(vm.views.value[1].description).toBe('Second')
  })

  it('removes the field when the description is cleared', async () => {
    const vm = manager()
    await vm.saveViewDescription('v2', '   ')
    expect('description' in vm.views.value[1]).toBe(false)
    expect(setPreference).toHaveBeenCalledTimes(1)
  })

  it('does not persist when nothing changed', async () => {
    const vm = manager()
    await vm.saveViewDescription('v2', '  Second ')
    await vm.saveViewDescription('v1', '')
    expect(setPreference).not.toHaveBeenCalled()
  })

  it('ignores an unknown view', async () => {
    const vm = manager()
    await vm.saveViewDescription('missing', 'Text')
    expect(setPreference).not.toHaveBeenCalled()
  })

  it('carries the description over when a view is duplicated', async () => {
    const vm = manager()
    await vm.duplicateView('v2')
    expect(vm.views.value[2].description).toBe('Second')
  })
})

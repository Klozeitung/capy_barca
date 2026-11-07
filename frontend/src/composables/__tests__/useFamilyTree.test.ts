import { describe, it, expect, vi } from 'vitest'

vi.mock('@/api/client', () => ({
  apiClient: { post: vi.fn().mockResolvedValue({}) },
  API_UNAUTHORIZED_EVENT: 'capybarca:unauthorized',
}))

import { apiClient } from '@/api/client'
import { familyTreeSettings, fetchFamilyTree, runFamilyTreeMapper } from '@/composables/useFamilyTree'
import type { DatabaseView } from '@/stores/database'

const view: DatabaseView = {
  id: 'view-1', name: 'Tree', viewType: 'family_tree', colOrder: ['a'], colWidths: {},
  filterGroups: [], sorts: [], hiddenColumns: [],
  familyTreeRootId: 'entry-1', familyTreeProperties: ['s-1'],
}

describe('useFamilyTree', () => {
  it('sends only the family tree settings that are set', () => {
    expect(familyTreeSettings(view)).toEqual({ familyTreeRootId: 'entry-1', familyTreeProperties: ['s-1'] })
  })

  it('asks for the tree and runs the mapper with the settings the user sees', async () => {
    const body = { settings: { familyTreeRootId: 'entry-1', familyTreeProperties: ['s-1'] } }
    await fetchFamilyTree('db-1', view)
    expect(apiClient.post).toHaveBeenCalledWith('/api/databases/db-1/views/view-1/family-tree', body)
    await runFamilyTreeMapper('db-1', view)
    expect(apiClient.post).toHaveBeenCalledWith('/api/databases/db-1/views/view-1/family-tree/map', body)
  })
})

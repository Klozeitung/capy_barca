import { describe, it, expect, vi } from 'vitest'

vi.mock('@/api/client', () => ({
  apiClient: { post: vi.fn().mockResolvedValue({}) },
  API_UNAUTHORIZED_EVENT: 'capybarca:unauthorized',
}))

import { apiClient } from '@/api/client'
import { fetchFamilyTree, runFamilyTreeMapper } from '@/composables/useFamilyTree'

describe('useFamilyTree', () => {
  it('asks for the tree and runs the mapper of a view', async () => {
    await fetchFamilyTree('db-1', 'view-1')
    expect(apiClient.post).toHaveBeenCalledWith('/api/databases/db-1/views/view-1/family-tree', {})
    await runFamilyTreeMapper('db-1', 'view-1')
    expect(apiClient.post).toHaveBeenCalledWith('/api/databases/db-1/views/view-1/family-tree/map', {})
  })
})

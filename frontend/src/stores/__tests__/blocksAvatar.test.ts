import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useBlockStore, type Block } from '@/stores/blocks'

vi.mock('@/api/client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
    postForm: vi.fn(),
  },
  API_UNAUTHORIZED_EVENT: 'capybarca:unauthorized',
}))

import { apiClient } from '@/api/client'

function page(avatar: string | null): Block {
  return {
    id: 'page-a', parent_id: null, reference_id: null, type: 'page', position: 1,
    state: 'active', content: null, icon: null, cover: null, avatar,
  }
}

describe('useBlockStore profile picture', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('uploads the cropped image as a form and stores the returned URL', async () => {
    const store = useBlockStore()
    store.blocks['page-a'] = page(null)
    const kept = store.blocks['page-a']
    vi.mocked(apiClient.postForm).mockResolvedValueOnce(page('/static/uploads/avatars/page-a.webp?v=1'))

    await store.uploadAvatar('page-a', new Blob(['x'], { type: 'image/webp' }), 'avatar.webp')

    const [url, form] = vi.mocked(apiClient.postForm).mock.calls[0]
    expect(url).toBe('/api/blocks/page-a/avatar')
    const file = (form as FormData).get('file') as File
    expect(file.name).toBe('avatar.webp')
    // Patched in place, so components holding the object see the change.
    expect(kept.avatar).toBe('/static/uploads/avatars/page-a.webp?v=1')
  })

  it('removes the picture through the delete endpoint', async () => {
    const store = useBlockStore()
    store.blocks['page-a'] = page('/static/uploads/avatars/page-a.png?v=1')
    vi.mocked(apiClient.delete).mockResolvedValueOnce(page(null))

    await store.removeAvatar('page-a')

    expect(apiClient.delete).toHaveBeenCalledWith('/api/blocks/page-a/avatar')
    expect(store.blocks['page-a'].avatar).toBeNull()
  })

  it('takes a new picture from an appearance event', () => {
    const store = useBlockStore()
    store.blocks['page-a'] = page(null)

    store._applyEvent({
      event_type: 'appearance_updated',
      block_id: 'page-a',
      before: page(null),
      after: page('/static/uploads/avatars/page-a.png?v=2'),
    } as never)

    expect(store.blocks['page-a'].avatar).toBe('/static/uploads/avatars/page-a.png?v=2')
  })
})

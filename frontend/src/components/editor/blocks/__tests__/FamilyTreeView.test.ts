import { describe, it, expect, afterEach, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key, locale: { value: 'en' } }),
}))

vi.mock('@/plugins/i18n', () => ({
  default: { global: { t: (key: string) => key } },
  setLocale: vi.fn(),
}))

vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  apiClient: { get: vi.fn(), getBlob: vi.fn(), post: vi.fn() },
}))

import FamilyTreeView from '../FamilyTreeView.vue'
import { apiClient } from '@/api/client'
import type { DatabaseView } from '@/stores/database'
import { sampleTree } from './familyTreeFixtures'

let app: App | null = null
let host: HTMLElement | null = null

function view(extra: Partial<DatabaseView> = {}): DatabaseView {
  return {
    id: 'view-1', name: 'Tree', viewType: 'family_tree', colOrder: [], colWidths: {},
    filterGroups: [], sorts: [], hiddenColumns: [], familyTreeRootId: 'Person C', ...extra,
  }
}

async function mount(props: { view: DatabaseView }, onOpen = vi.fn()) {
  setActivePinia(createPinia())
  host = document.createElement('div')
  document.body.appendChild(host)
  app = createApp({
    render: () => h(FamilyTreeView, { databaseId: 'db-1', viewName: 'Tree', ...props, onOpenEntry: onOpen }),
  })
  app.use(createPinia())
  app.mount(host)
  await new Promise((resolve) => setTimeout(resolve, 0))
  await nextTick()
  return onOpen
}

afterEach(() => {
  app?.unmount()
  host?.remove()
  app = null
  host = null
  vi.clearAllMocks()
})

describe('FamilyTreeView', () => {
  it('asks for a root while none is chosen', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ ...sampleTree(), root_valid: false })
    await mount({ view: view({ familyTreeRootId: undefined }) })
    expect(host?.textContent).toContain('db.familyTree.noRoot')
  })

  it('draws the people of the instant and opens an entry on click', async () => {
    vi.mocked(apiClient.post).mockResolvedValue(sampleTree())
    const onOpen = await mount({ view: view({ familyTreeAt: '2022-01-01' }) })
    expect(apiClient.post).toHaveBeenCalledWith('/api/databases/db-1/views/view-1/family-tree', {
      settings: { familyTreeRootId: 'Person C', familyTreeAt: '2022-01-01' },
    })
    const titles = Array.from(host!.querySelectorAll('.ft-node__title')).map((n) => n.textContent)
    expect(titles).toEqual(['Person A', 'Person B', 'Person C'])
    expect(host!.textContent).toContain('Value A')
    // Two parent links, the partnership; the outsider's overlay does not hold yet.
    expect(host!.querySelectorAll('.ft-link')).toHaveLength(3)
    expect(host!.querySelectorAll('.ft-overlay')).toHaveLength(0)

    ;(host!.querySelectorAll('.ft-node')[2] as SVGGElement).dispatchEvent(new MouseEvent('click', { bubbles: true }))
    expect(onOpen).toHaveBeenCalledWith('Person C')
  })
})

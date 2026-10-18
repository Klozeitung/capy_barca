import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key }),
}))

vi.mock('@iconify/vue', () => ({
  Icon: { render: () => null },
}))

const get = vi.fn()
const put = vi.fn()
vi.mock('@/api/client', () => ({
  apiClient: {
    get: (url: string) => get(url),
    put: (url: string, body: unknown) => put(url, body),
  },
}))

import PermissionsSection from '../PermissionsSection.vue'

// Mounted with a bare createApp, like the other component tests: with i18n,
// icons and the API client mocked, the component needs no plugins.

const USERS = [
  { id: 'user-a', username: 'Person A' },
  { id: 'user-b', username: 'Person B' },
]

function permission(overrides: Record<string, unknown> = {}) {
  return {
    block_id: 'block-x',
    mode: 'whitelist',
    everyone_level: 'write',
    owner_id: null,
    grants: [{ user_id: 'user-a', level: 'write' }],
    effective_mode: 'whitelist',
    inherited_from_id: null,
    can_edit: true,
    warn_accessibility: false,
    ...overrides,
  }
}

let app: App | null = null
let host: HTMLElement | null = null

async function flush(): Promise<void> {
  for (let i = 0; i < 4; i++) {
    await Promise.resolve()
    await nextTick()
  }
}

async function mount(data = permission()): Promise<HTMLElement> {
  get.mockImplementation(async (url: string) => (url.endsWith('/permissions') ? data : USERS))
  host = document.createElement('div')
  document.body.appendChild(host)
  app = createApp({ render: () => h(PermissionsSection, { blockId: 'block-x' }) })
  app.mount(host)
  await flush()
  return host
}

function saveButton(root: HTMLElement): HTMLButtonElement {
  return root.querySelector<HTMLButtonElement>('.perm__save-btn')!
}

beforeEach(() => {
  get.mockReset()
  put.mockReset()
})

afterEach(() => {
  app?.unmount()
  host?.remove()
  app = null
  host = null
})

describe('PermissionsSection', () => {
  it('shows the level of each grant', async () => {
    const root = await mount()
    const select = root.querySelector<HTMLSelectElement>('.perm__grant-level')!
    expect(select.value).toBe('write')
  })

  it('counts a changed grant level as a change and sends it', async () => {
    const root = await mount()
    expect(saveButton(root).disabled).toBe(true)

    const select = root.querySelector<HTMLSelectElement>('.perm__grant-level')!
    select.value = 'read'
    select.dispatchEvent(new Event('change'))
    await nextTick()
    expect(saveButton(root).disabled).toBe(false)

    put.mockResolvedValueOnce(permission({ grants: [{ user_id: 'user-a', level: 'read' }] }))
    saveButton(root).click()
    await flush()

    expect(put).toHaveBeenCalledWith('/api/blocks/block-x/permissions', {
      mode: 'whitelist',
      everyone_level: 'write',
      grants: [{ user_id: 'user-a', level: 'read' }],
    })
    expect(saveButton(root).disabled).toBe(true)
  })

  it('adds a new grant with read access', async () => {
    const root = await mount()
    const select = root.querySelector<HTMLSelectElement>('.perm__add-select')!
    select.value = 'user-b'
    select.dispatchEvent(new Event('change'))
    await nextTick()
    root.querySelector<HTMLButtonElement>('.perm__add-btn')!.click()
    await nextTick()

    const levels = [...root.querySelectorAll<HTMLSelectElement>('.perm__grant-level')]
    expect(levels.map(s => s.value)).toEqual(['write', 'read'])
  })

  it('offers the level for everyone only in that mode', async () => {
    const root = await mount(permission({ mode: 'everyone', grants: [] }))
    const buttons = [...root.querySelectorAll<HTMLButtonElement>('.perm__level-btn')]
    expect(buttons.map(b => b.textContent?.trim())).toEqual([
      'permissions.levelRead',
      'permissions.levelWrite',
    ])

    buttons[0].click()
    await nextTick()
    put.mockResolvedValueOnce(permission({ mode: 'everyone', everyone_level: 'read', grants: [] }))
    saveButton(root).click()
    await flush()

    expect(put.mock.calls[0][1]).toMatchObject({ mode: 'everyone', everyone_level: 'read' })
  })

  it('keeps the levels read-only for someone who may not change permissions', async () => {
    const root = await mount(permission({ can_edit: false }))
    expect(root.querySelector<HTMLSelectElement>('.perm__grant-level')!.disabled).toBe(true)
    expect(root.querySelector('.perm__save-btn')).toBeNull()
  })
})

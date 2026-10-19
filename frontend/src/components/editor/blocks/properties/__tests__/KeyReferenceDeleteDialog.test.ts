import { describe, it, expect, afterEach, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, string>) =>
      params ? `${key}:${Object.values(params).join('|')}` : key,
  }),
}))

vi.mock('@iconify/vue', () => ({
  Icon: { render: () => null },
}))

vi.mock('@/composables/useEscapeStack', () => ({
  useEscapeKey: () => undefined,
}))

import KeyReferenceDeleteDialog from '../KeyReferenceDeleteDialog.vue'
import type { KeyReference } from '@/stores/database'

let app: App | null = null
let host: HTMLElement | null = null

async function mount(references: KeyReference[]): Promise<string[]> {
  host = document.createElement('div')
  document.body.appendChild(host)
  app = createApp({
    render: () => h(KeyReferenceDeleteDialog, { references, propertyName: 'Field A' }),
  })
  app.mount(host)
  await nextTick()
  return [...document.querySelectorAll('.krd__list li')].map((li) => li.textContent?.trim() ?? '')
}

afterEach(() => {
  app?.unmount()
  host?.remove()
  app = null
  host = null
})

describe('KeyReferenceDeleteDialog', () => {
  it('names a readable relation and its database', async () => {
    const items = await mount([
      { schema_id: 's1', schema_name: 'Link A', database_id: 'd1', database_title: 'Database A' },
    ])
    expect(items).toEqual(['db.deleteColumnKeyedItem:Link A|Database A'])
  })

  it('falls back to the untitled label for a database without title', async () => {
    const items = await mount([
      { schema_id: 's1', schema_name: 'Link A', database_id: 'd1', database_title: null },
    ])
    expect(items).toEqual(['db.deleteColumnKeyedItem:Link A|main.untitled'])
  })

  it('lists a relation in an unreadable database without names', async () => {
    const items = await mount([
      { schema_id: 's1', schema_name: null, database_id: 'd1', database_title: null },
    ])
    expect(items).toEqual(['db.deleteColumnKeyedHidden'])
  })
})

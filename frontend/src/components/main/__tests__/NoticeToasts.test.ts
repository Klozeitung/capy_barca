import { describe, it, expect, afterEach, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key }),
}))

vi.mock('@iconify/vue', () => ({
  Icon: { render: () => null },
}))

import NoticeToasts from '../NoticeToasts.vue'
import { API_READ_ONLY_EVENT } from '@/api/client'
import { useNoticesStore } from '@/stores/notices'

let app: App | null = null
let host: HTMLElement | null = null

function mount(): HTMLElement {
  const pinia = createPinia()
  setActivePinia(pinia)
  host = document.createElement('div')
  document.body.appendChild(host)
  app = createApp({ render: () => h(NoticeToasts) })
  app.use(pinia)
  app.mount(host)
  return host
}

afterEach(() => {
  app?.unmount()
  host?.remove()
  app = null
  host = null
})

describe('NoticeToasts', () => {
  it('shows a notice when a change is refused as read-only', async () => {
    const root = mount()
    window.dispatchEvent(new CustomEvent(API_READ_ONLY_EVENT))
    await nextTick()

    expect(useNoticesStore().notices.map(n => n.key)).toEqual(['notices.readOnly'])
    expect(root.querySelector('.notices__text')?.textContent).toBe('notices.readOnly')
  })

  it('shows repeated refusals as one notice', async () => {
    mount()
    window.dispatchEvent(new CustomEvent(API_READ_ONLY_EVENT))
    window.dispatchEvent(new CustomEvent(API_READ_ONLY_EVENT))
    await nextTick()

    expect(useNoticesStore().notices).toHaveLength(1)
  })

  it('stops listening once unmounted', () => {
    mount()
    const store = useNoticesStore()
    app!.unmount()
    app = null
    window.dispatchEvent(new CustomEvent(API_READ_ONLY_EVENT))

    expect(store.notices).toHaveLength(0)
  })
})

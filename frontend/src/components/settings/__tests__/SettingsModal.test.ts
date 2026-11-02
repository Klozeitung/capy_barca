import { describe, it, expect, afterEach, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, string>) =>
      params ? `${key} ${Object.values(params).join(' ')}` : key,
    locale: { value: 'en' },
  }),
}))

vi.mock('@/plugins/i18n', () => ({
  default: { global: { t: (key: string) => key } },
  setLocale: vi.fn(),
}))

vi.mock('@iconify/vue', () => ({
  Icon: { render: () => null },
}))

vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  apiClient: { get: vi.fn(), getBlob: vi.fn(), patch: vi.fn(), post: vi.fn(), delete: vi.fn() },
}))

import SettingsModal from '../SettingsModal.vue'
import { apiClient } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

let app: App | null = null
let host: HTMLElement | null = null

const ANDROID_APP = {
  filename: 'capybarca-android-0.1.0.0-debug.apk',
  version: '0.1.0.0',
  debug: true,
  size: 2_097_152,
}

/** Mount the modal signed in with *role*; the server offers *android*. */
async function open(role: 'admin' | 'member', android: unknown): Promise<void> {
  vi.mocked(apiClient.get).mockImplementation(async (url: string) => {
    if (url === '/api/verify') return { authenticated: true, username: 'Person A', role }
    if (url === '/api/media/capacity') return { total_bytes: 1, used_bytes: 0, free_bytes: 1 }
    if (url === '/api/downloads/android') return android
    throw new Error(`unexpected ${url}`)
  })
  const pinia = createPinia()
  setActivePinia(pinia)
  await useAuthStore().verify()
  host = document.createElement('div')
  document.body.appendChild(host)
  app = createApp({ render: () => h(SettingsModal) })
  app.use(pinia)
  app.mount(host)
  await nextTick()

  const nav = [...document.body.querySelectorAll<HTMLButtonElement>('.settings-nav__item')]
  nav.find((b) => b.textContent?.includes('settings.downloads'))!.click()
  for (let i = 0; i < 4; i++) await nextTick()
}

function section(titleKey: string): Element | undefined {
  return [...document.body.querySelectorAll('.downloads-section')]
    .find((s) => s.querySelector('.downloads-section__title')?.textContent?.includes(titleKey))
}

afterEach(() => {
  app?.unmount()
  host?.remove()
  app = null
  host = null
  vi.clearAllMocks()
})

describe('SettingsModal downloads', () => {
  it('offers members the Android app but not the backup script', async () => {
    await open('member', ANDROID_APP)

    const link = document.body.querySelector<HTMLAnchorElement>('a.backup-download-btn')
    expect(link?.getAttribute('href')).toBe('/api/downloads/android/file')
    expect(link?.getAttribute('download')).toBe(ANDROID_APP.filename)
    expect(document.body.querySelector('.downloads-meta')?.textContent).toContain('0.1.0.0')
    expect(document.body.querySelector('.downloads-meta')?.textContent).toContain('2.0 MB')
    expect(section('settings.backup')).toBeUndefined()
  })

  it('shows admins the backup part as well', async () => {
    await open('admin', ANDROID_APP)

    expect(section('settings.androidTitle')).toBeDefined()
    expect(section('settings.backup')).toBeDefined()
  })

  it('says so when the server offers no app', async () => {
    await open('member', null)

    expect(document.body.querySelector('a.backup-download-btn')).toBeNull()
    expect(section('settings.androidTitle')?.textContent).toContain('settings.androidNone')
  })
})

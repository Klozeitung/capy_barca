import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key }),
}))

vi.mock('@/plugins/i18n', () => ({
  default: { global: { t: (key: string) => key } },
  SUPPORTED_LOCALES: ['de', 'en'],
  browserLocale: vi.fn(() => 'en'),
  setLocale: vi.fn(),
}))

vi.mock('@iconify/vue', () => ({
  Icon: { render: () => null },
}))

vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  apiClient: { get: vi.fn(), patch: vi.fn() },
}))

import PreferencesPrompt from '../PreferencesPrompt.vue'
import { apiClient } from '@/api/client'
import { setLocale } from '@/plugins/i18n'
import { useAuthStore } from '@/stores/auth'

let app: App | null = null
let host: HTMLElement | null = null

/** Mount the prompt for an account whose date format is *dateFormat*. */
async function mount(dateFormat = 'YYYY-MM-DD'): Promise<void> {
  const pinia = createPinia()
  setActivePinia(pinia)
  useAuthStore().setDateFormat(dateFormat)
  host = document.createElement('div')
  document.body.appendChild(host)
  app = createApp({ render: () => h(PreferencesPrompt) })
  app.use(pinia)
  app.mount(host)
  await nextTick()
}

function options(group: number): HTMLButtonElement[] {
  const groups = document.body.querySelectorAll('.pp__group')
  return [...groups[group].querySelectorAll<HTMLButtonElement>('.pp__option')]
}

function checked(group: number): string | undefined {
  return options(group).find((b) => b.getAttribute('aria-checked') === 'true')?.textContent?.trim()
}

async function settle(): Promise<void> {
  for (let i = 0; i < 4; i++) {
    await nextTick()
    await Promise.resolve()
  }
}

beforeEach(() => {
  vi.mocked(apiClient.patch).mockImplementation(async (url: string, body: unknown) => {
    if (url === '/api/users/me/language') {
      return { username: 'Person A', role: 'member', date_format: 'YYYY-MM-DD', ...(body as object) }
    }
    return {}
  })
})

afterEach(() => {
  app?.unmount()
  host?.remove()
  app = null
  host = null
  vi.clearAllMocks()
})

describe('PreferencesPrompt', () => {
  it('preselects the browser language and the current date format', async () => {
    await mount('YYYY-MM-DD')

    expect(checked(0)).toBe('settings.languageEn')
    expect(checked(1)).toBe('2026-12-31')
    expect(setLocale).toHaveBeenCalledWith('en')
  })

  it('switches the interface as soon as a language is picked', async () => {
    await mount()

    options(0)[0].click()
    await nextTick()

    expect(setLocale).toHaveBeenLastCalledWith('de')
    expect(checked(0)).toBe('settings.languageDe')
  })

  it('saves the date format first and the language last', async () => {
    await mount()
    options(1)[1].click()
    await nextTick()

    document.body.querySelector<HTMLButtonElement>('.pp__save')!.click()
    await settle()

    expect(vi.mocked(apiClient.patch).mock.calls).toEqual([
      ['/api/users/me/date-format', { date_format: 'MM.DD.YYYY' }],
      ['/api/users/me/language', { language: 'en' }],
    ])
    const auth = useAuthStore()
    expect(auth.dateFormat).toBe('MM.DD.YYYY')
    expect(auth.language).toBe('en')
  })

  it('reports a failed save and stores no language', async () => {
    vi.mocked(apiClient.patch).mockRejectedValueOnce(new Error('offline'))
    await mount()

    document.body.querySelector<HTMLButtonElement>('.pp__save')!.click()
    await settle()

    expect(document.body.querySelector('.pp__error')).not.toBeNull()
    expect(apiClient.patch).toHaveBeenCalledOnce()
    expect(useAuthStore().language).toBeNull()
  })
})

import { describe, it, expect, afterEach, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key }),
}))

vi.mock('@/plugins/i18n', () => ({
  default: { global: { t: (key: string) => key } },
}))

vi.mock('@iconify/vue', () => ({
  Icon: { render: () => null },
}))

import AlertShiftDialog from '../AlertShiftDialog.vue'
import { useAlertsStore } from '@/stores/alerts'
import { CALENDAR_DATE_MOVED_EVENT, type CalendarDateMove } from '@/composables/alertValue'

let app: App | null = null
let host: HTMLElement | null = null

function mount(): void {
  const pinia = createPinia()
  setActivePinia(pinia)
  host = document.createElement('div')
  document.body.appendChild(host)
  app = createApp({ render: () => h(AlertShiftDialog) })
  app.use(pinia)
  app.mount(host)
}

function moved(entryId: string, oldStart: string, newStart: string): void {
  const detail: CalendarDateMove = { databaseId: 'db-a', entryId, oldStart, newStart }
  window.dispatchEvent(new CustomEvent(CALENDAR_DATE_MOVED_EVENT, { detail }))
}

function dialog(): HTMLElement | null {
  return document.body.querySelector('.asd')
}

function option(index: number): HTMLButtonElement {
  return document.body.querySelectorAll<HTMLButtonElement>('.asd__option')[index]
}

async function settle(): Promise<void> {
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

afterEach(() => {
  app?.unmount()
  host?.remove()
  app = null
  host = null
  vi.restoreAllMocks()
})

describe('AlertShiftDialog', () => {
  it('stays closed until a date with own alerts is moved', async () => {
    mount()
    expect(dialog()).toBeNull()
    moved('entry-a', '2045-07-16', '2045-07-17')
    await nextTick()
    expect(dialog()).not.toBeNull()
  })

  it('keeping the alerts closes the dialog without asking the server', async () => {
    mount()
    const shift = vi.spyOn(useAlertsStore(), 'shiftAlerts').mockResolvedValue()
    moved('entry-a', '2045-07-16', '2045-07-17')
    await nextTick()

    option(0).click()
    await settle()

    expect(shift).not.toHaveBeenCalled()
    expect(dialog()).toBeNull()
  })

  it('shifting sends the chosen mode, then asks about the next entry', async () => {
    mount()
    const shift = vi.spyOn(useAlertsStore(), 'shiftAlerts').mockResolvedValue()
    moved('entry-a', '2045-07-16', '2045-07-17')
    moved('entry-b', '2045-07-20T10:00', '2045-07-21T10:00')
    await nextTick()

    option(2).click()
    await settle()

    expect(shift).toHaveBeenCalledWith(
      { databaseId: 'db-a', entryId: 'entry-a', oldStart: '2045-07-16', newStart: '2045-07-17' },
      'exact',
    )
    expect(dialog()).not.toBeNull()

    option(1).click()
    await settle()

    expect(shift).toHaveBeenLastCalledWith(
      { databaseId: 'db-a', entryId: 'entry-b', oldStart: '2045-07-20T10:00', newStart: '2045-07-21T10:00' },
      'day',
    )
    expect(dialog()).toBeNull()
  })

  it('merges repeated moves of one entry into one question', async () => {
    mount()
    const shift = vi.spyOn(useAlertsStore(), 'shiftAlerts').mockResolvedValue()
    moved('entry-a', '2045-07-16', '2045-07-17')
    moved('entry-a', '2045-07-17', '2045-07-19')
    await nextTick()

    option(1).click()
    await settle()

    expect(shift).toHaveBeenCalledTimes(1)
    expect(shift.mock.calls[0][0].oldStart).toBe('2045-07-16')
    expect(shift.mock.calls[0][0].newStart).toBe('2045-07-19')
    expect(dialog()).toBeNull()
  })

  it('a failed shift keeps the question open and says so', async () => {
    mount()
    vi.spyOn(useAlertsStore(), 'shiftAlerts').mockRejectedValue(new Error('offline'))
    moved('entry-a', '2045-07-16', '2045-07-17')
    await nextTick()

    option(1).click()
    await settle()

    expect(dialog()).not.toBeNull()
    expect(document.body.querySelector('.asd__error')).not.toBeNull()
  })
})

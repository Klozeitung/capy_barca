import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useRecentIconsStore, RECENT_ICONS_LIMIT, type RecentIcon } from '@/stores/recentIcons'
import { useAuthStore } from '@/stores/auth'

// ── Helpers ───────────────────────────────────────────────────────────────────

function respond(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response
}

function entries(...icons: string[]): RecentIcon[] {
  return icons.map((icon, i) => ({ icon, used_at: `2000-07-28T00:00:0${i}+00:00` }))
}

/** A response that resolves only when the test says so. */
function deferred(): { promise: Promise<Response>; resolve: (r: Response) => void } {
  let resolve!: (r: Response) => void
  const promise = new Promise<Response>((r) => { resolve = r })
  return { promise, resolve }
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('useRecentIconsStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.stubGlobal('fetch', vi.fn())
    useAuthStore().login('person-a', 'member')
  })

  it('loads the list once per signed-in user', async () => {
    vi.mocked(fetch).mockResolvedValue(respond(entries('mdi:alpha-a', 'mdi:alpha-b')))
    const store = useRecentIconsStore()

    await store.ensureLoaded()
    await store.ensureLoaded()

    expect(store.icons).toEqual(['mdi:alpha-a', 'mdi:alpha-b'])
    expect(fetch).toHaveBeenCalledOnce()
  })

  it('loads again for another user', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(respond(entries('mdi:alpha-a')))
      .mockResolvedValueOnce(respond(entries('mdi:alpha-b')))
    const store = useRecentIconsStore()
    await store.ensureLoaded()

    useAuthStore().login('person-b', 'member')
    await store.ensureLoaded()

    expect(store.icons).toEqual(['mdi:alpha-b'])
  })

  it('shows a pick at once and then takes the list the backend returns', async () => {
    const answer = deferred()
    vi.mocked(fetch).mockReturnValueOnce(answer.promise)
    const store = useRecentIconsStore()
    store.icons = ['mdi:alpha-a', 'mdi:alpha-b']

    const recording = store.recordUse('mdi:alpha-b')
    expect(store.icons).toEqual(['mdi:alpha-b', 'mdi:alpha-a'])

    answer.resolve(respond(entries('mdi:alpha-b', 'mdi:alpha-a', 'mdi:alpha-c')))
    await recording

    expect(store.icons).toEqual(['mdi:alpha-b', 'mdi:alpha-a', 'mdi:alpha-c'])
    const [url, init] = vi.mocked(fetch).mock.calls[0]
    expect(url).toBe('/api/users/me/recent-icons')
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ icon: 'mdi:alpha-b' })
  })

  it('drops the icon used longest ago when a new one fills the list', async () => {
    vi.mocked(fetch).mockReturnValueOnce(new Promise(() => undefined))
    const store = useRecentIconsStore()
    const full = Array.from({ length: RECENT_ICONS_LIMIT }, (_, n) => `mdi:numeric-${n}`)
    store.icons = [...full]

    void store.recordUse('mdi:numeric-x')

    expect(store.icons).toHaveLength(RECENT_ICONS_LIMIT)
    expect(store.icons[0]).toBe('mdi:numeric-x')
    expect(store.icons).not.toContain(full[RECENT_ICONS_LIMIT - 1])
  })

  it('keeps the local list when recording fails', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(respond({ detail: 'oops' }, 500))
    const store = useRecentIconsStore()

    await store.recordUse('mdi:alpha-a')

    expect(store.icons).toEqual(['mdi:alpha-a'])
  })

  it('does not let a slower initial load undo a pick', async () => {
    const load = deferred()
    vi.mocked(fetch)
      .mockReturnValueOnce(load.promise)
      .mockResolvedValueOnce(respond(entries('mdi:alpha-x', 'mdi:alpha-a')))
    const store = useRecentIconsStore()

    const loading = store.ensureLoaded()
    await store.recordUse('mdi:alpha-x')
    load.resolve(respond(entries('mdi:alpha-a')))
    await loading

    expect(store.icons).toEqual(['mdi:alpha-x', 'mdi:alpha-a'])
  })
})

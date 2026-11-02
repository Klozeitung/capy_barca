import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { getIcon, iconLoaded, loadIcon } from '@iconify/vue'
import { useCustomIconsStore, type CustomIcon } from '@/stores/customIcons'
import { WS_CUSTOM_ICONS_EVENT } from '@/stores/ws'

// ── Helpers ───────────────────────────────────────────────────────────────────

function respond(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response
}

function icon(id: string, name = 'Icon A'): CustomIcon {
  return {
    id,
    icon: `custom:${id}`,
    name,
    url: `/static/uploads/icons/${id}.png`,
    created_at: '2000-07-28T00:00:00+00:00',
  }
}

const A = icon('0a000000-0000-4000-8000-00000000000a')
const B = icon('0b000000-0000-4000-8000-00000000000b', 'Icon B')
const C = icon('0c000000-0000-4000-8000-00000000000c', 'Icon C')

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('useCustomIconsStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.stubGlobal('fetch', vi.fn())
  })

  it('loads the list once and registers every icon with Iconify', async () => {
    vi.mocked(fetch).mockResolvedValue(respond([A, B]))
    const store = useCustomIconsStore()

    await store.ensureLoaded()
    await store.ensureLoaded()

    expect(fetch).toHaveBeenCalledOnce()
    expect(store.icons).toEqual([A, B])
    expect(iconLoaded(A.icon)).toBe(true)
    expect(getIcon(B.icon)?.body).toContain(B.url)
  })

  it('treats bundled icons as available and deleted uploads as missing', async () => {
    vi.mocked(fetch).mockResolvedValue(respond([A]))
    const store = useCustomIconsStore()

    expect(store.isAvailable(C.icon)).toBe(true)
    await store.ensureLoaded()

    expect(store.isAvailable('mdi:star-outline')).toBe(true)
    expect(store.isAvailable(A.icon)).toBe(true)
    expect(store.isAvailable(C.icon)).toBe(false)
    expect(store.isAvailable(null)).toBe(false)
    expect(store.find(A.icon)?.name).toBe('Icon A')
  })

  it('a failed load leaves the list empty and can be retried', async () => {
    vi.mocked(fetch).mockRejectedValueOnce(new Error('offline'))
    const store = useCustomIconsStore()

    await store.ensureLoaded()
    expect(store.loaded).toBe(false)

    vi.mocked(fetch).mockResolvedValue(respond([A]))
    await store.ensureLoaded()
    expect(store.icons).toEqual([A])
  })

  it('takes the list the server announces', async () => {
    vi.mocked(fetch).mockResolvedValue(respond([A]))
    const store = useCustomIconsStore()
    await store.ensureLoaded()

    window.dispatchEvent(new CustomEvent(WS_CUSTOM_ICONS_EVENT, { detail: { icons: [B] } }))

    expect(store.icons).toEqual([B])
    expect(iconLoaded(B.icon)).toBe(true)
  })

  it('uploads a file as a form and lists the new icon', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(respond([A]))
    const store = useCustomIconsStore()
    await store.ensureLoaded()
    vi.mocked(fetch).mockResolvedValueOnce(respond(B, 201))

    const created = await store.upload(new File([new Uint8Array([1])], 'Icon B.png'))

    expect(created).toEqual(B)
    expect(store.icons).toEqual([A, B])
    const [url, init] = vi.mocked(fetch).mock.calls[1]
    expect(url).toBe('/api/icons')
    expect((init as RequestInit).body).toBeInstanceOf(FormData)
  })

  it('removes a deleted icon from the list', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(respond([A, B]))
    const store = useCustomIconsStore()
    await store.ensureLoaded()
    vi.mocked(fetch).mockResolvedValueOnce(respond(null, 204))

    await store.remove(A.id)

    expect(store.icons).toEqual([B])
    const [url, init] = vi.mocked(fetch).mock.calls[1]
    expect(url).toBe(`/api/icons/${A.id}`)
    expect((init as RequestInit).method).toBe('DELETE')
  })

  it('answers Iconify for an icon it has not heard of by loading the list again', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(respond([A]))
      .mockResolvedValueOnce(respond([A, C]))
    const store = useCustomIconsStore()
    await store.ensureLoaded()

    const data = await loadIcon(C.icon)

    expect(fetch).toHaveBeenCalledTimes(2)
    expect(data.body).toContain(C.url)
  })
})

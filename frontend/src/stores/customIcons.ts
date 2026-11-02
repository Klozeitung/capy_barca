import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { apiClient } from '@/api/client'
import {
  CUSTOM_ICON_PREFIX,
  isCustomIcon,
  registerCustomIcon,
  setCustomIconSource,
} from '@/plugins/icons'
import { WS_CUSTOM_ICONS_EVENT } from '@/stores/ws'

const ICONS_URL = '/api/icons'

/** One uploaded icon as ``GET /api/icons`` returns it. */
export interface CustomIcon {
  id: string
  /** The string stored wherever the icon is used: ``custom:<id>``. */
  icon: string
  name: string
  url: string
  created_at: string
}

/**
 * The icons admins uploaded, which every account may pick.
 *
 * The list is loaded once per page load, after login, and kept current by
 * the ``custom_icons.changed`` message every upload and deletion sends.
 * Each icon is registered with Iconify as soon as it is known, so every
 * <Icon> renders it. Iconify asks this store for a custom icon it has no
 * data for (``setCustomIconSource``); an icon this page has not heard of yet
 * makes it load the list again, once.
 *
 * Uploading and deleting are for admins; the server refuses them otherwise.
 */
export const useCustomIconsStore = defineStore('customIcons', () => {
  const icons = ref<CustomIcon[]>([])
  const loaded = ref(false)

  let _loading: Promise<void> | null = null

  const byIcon = computed(() => new Map(icons.value.map((icon) => [icon.icon, icon])))

  function _adopt(list: CustomIcon[]): void {
    for (const icon of list) registerCustomIcon(icon.icon, icon.url)
    icons.value = list
    loaded.value = true
  }

  /** Load the list from the server; concurrent calls share one request. */
  function load(): Promise<void> {
    if (_loading) return _loading
    _loading = apiClient
      .get<CustomIcon[]>(ICONS_URL)
      .then(_adopt)
      .catch(() => undefined)
      .finally(() => {
        _loading = null
      })
    return _loading
  }

  /** Load the list unless it is already there. */
  function ensureLoaded(): Promise<void> {
    return loaded.value ? Promise.resolve() : load()
  }

  /**
   * Whether *icon* can be drawn: true for every bundled icon, and for an
   * uploaded one that still exists. Before the list has loaded, uploaded
   * icons count as present, so nothing switches to a fallback while
   * loading.
   */
  function isAvailable(icon: string | null | undefined): boolean {
    if (!icon) return false
    if (!isCustomIcon(icon)) return true
    return !loaded.value || byIcon.value.has(icon)
  }

  /** The uploaded icon *icon* names, if it exists. */
  function find(icon: string | null | undefined): CustomIcon | undefined {
    return icon ? byIcon.value.get(icon) : undefined
  }

  /** Upload *file* as a new icon (admins only) and return it. */
  async function upload(file: File): Promise<CustomIcon> {
    const form = new FormData()
    form.append('file', file)
    const created = await apiClient.postForm<CustomIcon>(ICONS_URL, form)
    if (!byIcon.value.has(created.icon)) _adopt([...icons.value, created])
    return created
  }

  /** Delete the icon with *id* (admins only). */
  async function remove(id: string): Promise<void> {
    await apiClient.delete<void>(`${ICONS_URL}/${id}`)
    icons.value = icons.value.filter((icon) => icon.id !== id)
  }

  // The image URL of the custom icon *name* (its id), for Iconify.
  let _refetched = false
  async function _source(name: string): Promise<string | null> {
    const icon = `${CUSTOM_ICON_PREFIX}:${name}`
    await ensureLoaded()
    if (!byIcon.value.has(icon) && !_refetched) {
      _refetched = true
      await load()
    }
    return byIcon.value.get(icon)?.url ?? null
  }
  setCustomIconSource(_source)

  window.addEventListener(WS_CUSTOM_ICONS_EVENT, (event: Event) => {
    const payload = (event as CustomEvent<{ icons?: CustomIcon[] }>).detail
    if (Array.isArray(payload?.icons)) _adopt(payload.icons)
  })

  return { icons, loaded, ensureLoaded, load, isAvailable, find, upload, remove }
})

import { ref } from 'vue'
import { defineStore } from 'pinia'
import { apiClient } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

/** How many icons the backend keeps per user (``RECENT_ICONS_LIMIT``). */
export const RECENT_ICONS_LIMIT = 10

const RECENT_ICONS_URL = '/api/users/me/recent-icons'

/** One entry of the list as the backend returns it. */
export interface RecentIcon {
  icon: string
  used_at: string
}

/**
 * The icons the current user picked most recently, newest first.
 *
 * The list belongs to the account and lives in the backend, so it is the
 * same on every device. It is loaded once per signed-in user, the first time
 * the icon picker opens, and updated with every icon the user picks there.
 * The backend decides the order and which entry a new icon replaces; the
 * store applies the same rule locally so the picker reflects a choice at
 * once, then takes the list the backend returns.
 *
 * Failures are ignored: the list is a convenience, and the picker works the
 * same without it.
 */
export const useRecentIconsStore = defineStore('recentIcons', () => {
  const auth = useAuthStore()

  const icons = ref<string[]>([])

  /** The user the list was loaded for; another sign-in loads it again. */
  let _loadedFor: string | null = null
  let _loading: Promise<void> | null = null
  /** Counts recorded uses, so a slower initial load cannot undo one. */
  let _recorded = 0

  function _adopt(list: RecentIcon[]): void {
    icons.value = list.map((entry) => entry.icon)
  }

  /** Load the list unless it is already there for the signed-in user. */
  function ensureLoaded(): Promise<void> {
    const user = auth.username
    if (_loadedFor === user) return Promise.resolve()
    if (_loading) return _loading
    const recordedBefore = _recorded
    _loading = apiClient
      .get<RecentIcon[]>(RECENT_ICONS_URL)
      .then((list) => {
        if (auth.username !== user) return
        if (_recorded === recordedBefore) _adopt(list)
        _loadedFor = user
      })
      .catch(() => undefined)
      .finally(() => {
        _loading = null
      })
    return _loading
  }

  /** Note that the user picked *icon*. */
  async function recordUse(icon: string): Promise<void> {
    _recorded++
    icons.value = [icon, ...icons.value.filter((i) => i !== icon)].slice(0, RECENT_ICONS_LIMIT)
    try {
      _adopt(await apiClient.post<RecentIcon[]>(RECENT_ICONS_URL, { icon }))
    } catch {
      // Non-critical; the local list already shows the choice.
    }
  }

  return { icons, ensureLoaded, recordUse }
})

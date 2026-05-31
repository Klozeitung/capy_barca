/**
 * Users store
 *
 * Maintains a client-side cache of user_id → username mappings used to
 * resolve the ``created_by`` / ``last_edited_by`` database properties.
 *
 * The cache is populated lazily on first access via GET /api/users/names,
 * which is available to all authenticated users (not admin-only).
 *
 * Usage:
 *   const usersStore = useUsersStore()
 *   const name = usersStore.resolveUser(someUUID)
 */
import { ref } from 'vue'
import { defineStore } from 'pinia'
import { apiClient } from '@/api/client'

interface UserNameEntry {
  id: string
  username: string
}

export const useUsersStore = defineStore('users', () => {
  const _cache = ref<Map<string, string>>(new Map())
  const _loaded = ref(false)
  const _loading = ref(false)

  /**
   * Fetch all user id/username pairs and populate the cache.
   * No-op if already loaded or a load is in flight.
   */
  async function loadUsers(): Promise<void> {
    if (_loaded.value || _loading.value) return
    _loading.value = true
    try {
      const users = await apiClient.get<UserNameEntry[]>('/api/users/names')
      for (const u of users) {
        _cache.value.set(u.id, u.username)
      }
      _loaded.value = true
    } catch {
      // Silently ignore – resolveUser will fall back to the raw UUID string.
    } finally {
      _loading.value = false
    }
  }

  /**
   * Resolve a user UUID to a display name.
   *
   * Returns the cached username if available, otherwise the raw UUID string
   * as a graceful fallback (prevents blank cells while the cache loads).
   */
  function resolveUser(userId: string): string {
    if (!_loaded.value && !_loading.value) {
      // Trigger a background load; the cell will re-render once the cache
      // is populated because _cache is reactive.
      loadUsers()
    }
    return _cache.value.get(userId) ?? userId
  }

  /**
   * Manually insert or update a single entry (e.g. after a username change).
   */
  function setUser(id: string, username: string): void {
    _cache.value.set(id, username)
  }

  /**
   * Invalidate the cache so the next resolveUser call triggers a fresh fetch.
   * Call this after any user management action that may change usernames.
   */
  function invalidate(): void {
    _loaded.value = false
  }

  return { resolveUser, setUser, invalidate, loadUsers }
})

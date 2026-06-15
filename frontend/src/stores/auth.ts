import { ref, computed, onUnmounted } from 'vue'
import { defineStore } from 'pinia'
import { apiClient, API_UNAUTHORIZED_EVENT } from '@/api/client'

/** Fallback display date format when the backend supplies none. */
const DEFAULT_DATE_FORMAT = 'DD.MM.YYYY'

export const useAuthStore = defineStore('auth', () => {
  const isAuthenticated = ref(false)
  const _username = ref('')
  const _role = ref<'admin' | 'member' | ''>('')
  const _dateFormat = ref<string>(DEFAULT_DATE_FORMAT)

  const username = computed(() => _username.value)
  const role = computed(() => _role.value)
  const isAdmin = computed(() => _role.value === 'admin')
  /**
   * The current user's preferred display date format token (e.g. "DD.MM.YYYY").
   * Consumed by the cell renderers as the global default; individual date
   * properties may still override it locally.
   */
  const dateFormat = computed(() => _dateFormat.value)

  /**
   * When any API call returns 401, the client broadcasts an event so that
   * auth state is cleared globally without polling or circular imports.
   */
  function _handleUnauthorized(): void {
    isAuthenticated.value = false
    _username.value = ''
    _role.value = ''
    _dateFormat.value = DEFAULT_DATE_FORMAT
  }

  window.addEventListener(API_UNAUTHORIZED_EVENT, _handleUnauthorized)

  // Clean up the listener if the store is ever disposed (e.g. during tests).
  onUnmounted(() => {
    window.removeEventListener(API_UNAUTHORIZED_EVENT, _handleUnauthorized)
  })

  async function verify(): Promise<void> {
    try {
      const data = await apiClient.get<{
        authenticated: boolean
        username: string
        role: string
        date_format?: string
      }>('/api/verify')
      isAuthenticated.value = true
      _username.value = data.username
      _role.value = data.role as 'admin' | 'member'
      _dateFormat.value = data.date_format || DEFAULT_DATE_FORMAT
    } catch {
      isAuthenticated.value = false
      _username.value = ''
      _role.value = ''
      _dateFormat.value = DEFAULT_DATE_FORMAT
    }
  }

  function login(username: string, role: string, dateFmt?: string): void {
    isAuthenticated.value = true
    _username.value = username
    _role.value = role as 'admin' | 'member'
    if (dateFmt) _dateFormat.value = dateFmt
  }

  /** Update the cached date-format preference after a settings change. */
  function setDateFormat(fmt: string): void {
    _dateFormat.value = fmt || DEFAULT_DATE_FORMAT
  }

  async function logout(): Promise<void> {
    try {
      await apiClient.post('/api/logout')
    } catch {
      // Session-Cleanup failed – reset local state anyway.
    }
    isAuthenticated.value = false
    _username.value = ''
    _role.value = ''
    _dateFormat.value = DEFAULT_DATE_FORMAT
  }

  return {
    isAuthenticated, username, role, isAdmin, dateFormat,
    login, logout, verify, setDateFormat,
  }
})

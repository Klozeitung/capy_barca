import { ref, computed, onUnmounted } from 'vue'
import { defineStore } from 'pinia'
import { apiClient, API_UNAUTHORIZED_EVENT } from '@/api/client'

export const useAuthStore = defineStore('auth', () => {
  const isAuthenticated = ref(false)
  const _username = ref('')
  const _role = ref<'admin' | 'member' | ''>('')

  const username = computed(() => _username.value)
  const role = computed(() => _role.value)
  const isAdmin = computed(() => _role.value === 'admin')

  /**
   * When any API call returns 401, the client broadcasts an event so that
   * auth state is cleared globally without polling or circular imports.
   */
  function _handleUnauthorized(): void {
    isAuthenticated.value = false
    _username.value = ''
    _role.value = ''
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
      }>('/api/verify')
      isAuthenticated.value = true
      _username.value = data.username
      _role.value = data.role as 'admin' | 'member'
    } catch {
      isAuthenticated.value = false
      _username.value = ''
      _role.value = ''
    }
  }

  function login(username: string, role: string): void {
    isAuthenticated.value = true
    _username.value = username
    _role.value = role as 'admin' | 'member'
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
  }

  return { isAuthenticated, username, role, isAdmin, login, logout, verify }
})

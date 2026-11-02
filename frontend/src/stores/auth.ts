import { ref, computed, onUnmounted } from 'vue'
import { defineStore } from 'pinia'
import { apiClient, API_UNAUTHORIZED_EVENT, browserTimeZone } from '@/api/client'
import { setLocale, type SupportedLocale } from '@/plugins/i18n'

/** Fallback display date format when the backend supplies none. */
const DEFAULT_DATE_FORMAT = 'DD.MM.YYYY'

/** The display date formats an account may choose (``DateFormatToken`` on the server). */
export const DATE_FORMAT_OPTIONS = ['DD.MM.YYYY', 'MM.DD.YYYY', 'YYYY-MM-DD', 'YYYY-DD-MM'] as const
export type DateFormatToken = typeof DATE_FORMAT_OPTIONS[number]

/** A fixed sample date (31 Dec 2026) in *fmt*, to show what a format looks like. */
export function dateFormatSample(fmt: DateFormatToken): string {
  const y = '2026', m = '12', d = '31'
  switch (fmt) {
    case 'MM.DD.YYYY': return `${m}.${d}.${y}`
    case 'YYYY-MM-DD': return `${y}-${m}-${d}`
    case 'YYYY-DD-MM': return `${y}-${d}-${m}`
    case 'DD.MM.YYYY':
    default:           return `${d}.${m}.${y}`
  }
}

/** The account as ``GET /api/users/me`` returns it, as far as it is used here. */
interface Profile {
  username: string
  role: string
  date_format: string
  language: SupportedLocale | null
}

export const useAuthStore = defineStore('auth', () => {
  const isAuthenticated = ref(false)
  const _username = ref('')
  const _role = ref<'admin' | 'member' | ''>('')
  const _dateFormat = ref<string>(DEFAULT_DATE_FORMAT)
  /**
   * The language stored with the account; null until the account has chosen
   * one. ``profileLoaded`` tells "not chosen" apart from "not loaded yet".
   */
  const _language = ref<SupportedLocale | null>(null)
  const _profileLoaded = ref(false)

  const username = computed(() => _username.value)
  const role = computed(() => _role.value)
  const isAdmin = computed(() => _role.value === 'admin')
  /**
   * The current user's preferred display date format token (e.g. "DD.MM.YYYY").
   * Consumed by the cell renderers as the global default; individual date
   * properties may still override it locally.
   */
  const dateFormat = computed(() => _dateFormat.value)
  const language = computed(() => _language.value)
  const profileLoaded = computed(() => _profileLoaded.value)
  /** True once the profile is loaded and the account has no language yet. */
  const needsPreferences = computed(() => _profileLoaded.value && _language.value === null)

  function _clearProfile(): void {
    _language.value = null
    _profileLoaded.value = false
  }

  /**
   * When any API call returns 401, the client broadcasts an event so that
   * auth state is cleared globally without polling or circular imports.
   */
  function _handleUnauthorized(): void {
    isAuthenticated.value = false
    _username.value = ''
    _role.value = ''
    _dateFormat.value = DEFAULT_DATE_FORMAT
    _clearProfile()
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

  /**
   * Load the account's profile and apply the language stored with it. An
   * account without a language keeps the language the browser last used and
   * is asked for one (``needsPreferences``). Failures leave the profile
   * unloaded, so nobody is asked on the strength of a failed request.
   */
  async function loadProfile(): Promise<void> {
    try {
      const profile = await apiClient.get<Profile>('/api/users/me')
      _dateFormat.value = profile.date_format || DEFAULT_DATE_FORMAT
      _language.value = profile.language
      _profileLoaded.value = true
      if (profile.language) setLocale(profile.language)
    } catch {
      // Not essential: the application works with the browser's language.
    }
  }

  /**
   * Store *language* with the account and switch the interface to it. The
   * interface switches even when saving fails, so the choice takes effect.
   */
  async function setLanguage(language: SupportedLocale): Promise<void> {
    setLocale(language)
    const profile = await apiClient.patch<Profile>('/api/users/me/language', { language })
    _language.value = profile.language
    _profileLoaded.value = true
  }

  /**
   * Tell the server the browser's time zone, so the account's calendar
   * alerts and requests without a zone of their own use it. The server
   * stores it only when it changed. Failures are ignored; the next start
   * tries again.
   */
  async function reportTimezone(): Promise<void> {
    const timezone = browserTimeZone()
    if (!timezone) return
    try {
      await apiClient.patch('/api/users/me/timezone', { timezone })
    } catch {
      // Not essential: requests name their zone themselves.
    }
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
    _clearProfile()
  }

  return {
    isAuthenticated, username, role, isAdmin, dateFormat,
    language, profileLoaded, needsPreferences,
    login, logout, verify, setDateFormat, reportTimezone, loadProfile, setLanguage,
  }
})

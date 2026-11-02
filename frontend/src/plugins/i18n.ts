import { createI18n } from 'vue-i18n'
import de from '@/locales/de.json'
import en from '@/locales/en.json'

/** The languages the application ships locales for. */
export type SupportedLocale = 'de' | 'en'

export const SUPPORTED_LOCALES: readonly SupportedLocale[] = ['de', 'en']

const LOCALE_STORAGE_KEY = 'capybarca-locale'

function isSupported(value: string | null | undefined): value is SupportedLocale {
  return !!value && (SUPPORTED_LOCALES as readonly string[]).includes(value)
}

/**
 * The language this browser used last. It applies until the account's own
 * language is loaded after login, which makes it the language of the login
 * page.
 */
const savedLocale: SupportedLocale = (() => {
  try {
    const stored = localStorage.getItem(LOCALE_STORAGE_KEY)
    return isSupported(stored) ? stored : 'de'
  } catch {
    return 'de'
  }
})()

const i18n = createI18n({
  legacy: false,
  locale: savedLocale,
  fallbackLocale: 'en',
  messages: { de, en },
})

export default i18n

/**
 * The supported language closest to the browser's own preference, or the
 * active one when the browser prefers none of them. Offered first when an
 * account is asked for its language.
 */
export function browserLocale(): SupportedLocale {
  const preferred = typeof navigator === 'undefined'
    ? []
    : (navigator.languages?.length ? navigator.languages : [navigator.language])
  for (const tag of preferred) {
    const base = (tag ?? '').toLowerCase().split('-')[0]
    if (isSupported(base)) return base
  }
  const active = i18n.global.locale.value
  return isSupported(active) ? active : 'de'
}

/**
 * Change the active locale and remember it in this browser. Storing it with
 * the account is the auth store's job (``setLanguage``).
 */
export function setLocale(locale: SupportedLocale): void {
  i18n.global.locale.value = locale
  try {
    localStorage.setItem(LOCALE_STORAGE_KEY, locale)
  } catch {
    // Private mode or blocked storage: the choice applies to this page only.
  }
}

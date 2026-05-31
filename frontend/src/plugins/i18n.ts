import { createI18n } from 'vue-i18n'
import de from '@/locales/de.json'
import en from '@/locales/en.json'

const savedLocale = localStorage.getItem('capybarca-locale') ?? 'de'

const i18n = createI18n({
  legacy: false,
  locale: savedLocale,
  fallbackLocale: 'en',
  messages: { de, en },
})

export default i18n

/**
 * Change the active locale and persist the selection.
 */
export function setLocale(locale: 'de' | 'en'): void {
  i18n.global.locale.value = locale
  localStorage.setItem('capybarca-locale', locale)
}

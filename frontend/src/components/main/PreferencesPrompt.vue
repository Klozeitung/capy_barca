<script setup lang="ts">
/**
 * PreferencesPrompt
 *
 * Asks an account that has not chosen a language yet for its language and
 * date format, once after login (``auth.needsPreferences``). The language
 * the browser prefers and the account's current date format are preselected.
 * Picking a language switches the interface at once, so the rest of the
 * dialog already reads in it.
 *
 * Saving stores the date format first and the language last: the stored
 * language is what marks the question as answered, so a save that fails
 * half-way asks again next time. The dialog cannot be dismissed without
 * saving; both settings can be changed later in the settings.
 */
import { ref } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { apiClient } from '@/api/client'
import {
  useAuthStore,
  DATE_FORMAT_OPTIONS,
  dateFormatSample,
  type DateFormatToken,
} from '@/stores/auth'
import {
  browserLocale,
  setLocale,
  SUPPORTED_LOCALES,
  type SupportedLocale,
} from '@/plugins/i18n'

const { t } = useI18n()
const auth = useAuthStore()

const LANGUAGE_LABELS: Record<SupportedLocale, string> = {
  de: 'settings.languageDe',
  en: 'settings.languageEn',
}

function initialDateFormat(): DateFormatToken {
  const current = auth.dateFormat as DateFormatToken
  return DATE_FORMAT_OPTIONS.includes(current) ? current : DATE_FORMAT_OPTIONS[0]
}

const language = ref<SupportedLocale>(browserLocale())
const dateFormat = ref<DateFormatToken>(initialDateFormat())
const saving = ref(false)
const failed = ref(false)

setLocale(language.value)

function pickLanguage(code: SupportedLocale): void {
  language.value = code
  setLocale(code)
}

async function save(): Promise<void> {
  if (saving.value) return
  saving.value = true
  failed.value = false
  try {
    await apiClient.patch('/api/users/me/date-format', { date_format: dateFormat.value })
    auth.setDateFormat(dateFormat.value)
    await auth.setLanguage(language.value)
  } catch {
    failed.value = true
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <Teleport to="body">
    <div class="pp__backdrop"></div>
    <div class="pp" role="dialog" aria-modal="true" :aria-label="t('preferences.title')">
      <div class="pp__header">
        <Icon icon="mdi:account-cog-outline" width="16" height="16" class="pp__icon" />
        <span class="pp__title">{{ t('preferences.title') }}</span>
      </div>
      <p class="pp__text">{{ t('preferences.text') }}</p>

      <div class="pp__group" role="radiogroup" :aria-label="t('settings.language')">
        <span class="pp__label">{{ t('settings.language') }}</span>
        <div class="pp__options">
          <button
            v-for="code in SUPPORTED_LOCALES"
            :key="code"
            type="button"
            role="radio"
            class="pp__option"
            :class="{ 'pp__option--active': language === code }"
            :aria-checked="language === code"
            :disabled="saving"
            @click="pickLanguage(code)"
          >
            <span>{{ t(LANGUAGE_LABELS[code]) }}</span>
            <Icon v-if="language === code" icon="mdi:check" width="14" height="14" />
          </button>
        </div>
      </div>

      <div class="pp__group" role="radiogroup" :aria-label="t('settings.date')">
        <span class="pp__label">{{ t('settings.date') }}</span>
        <div class="pp__options">
          <button
            v-for="fmt in DATE_FORMAT_OPTIONS"
            :key="fmt"
            type="button"
            role="radio"
            class="pp__option"
            :class="{ 'pp__option--active': dateFormat === fmt }"
            :aria-checked="dateFormat === fmt"
            :disabled="saving"
            @click="dateFormat = fmt"
          >
            <span>{{ dateFormatSample(fmt) }}</span>
            <Icon v-if="dateFormat === fmt" icon="mdi:check" width="14" height="14" />
          </button>
        </div>
      </div>

      <p class="pp__hint">{{ t('preferences.hint') }}</p>
      <p v-if="failed" class="pp__error" role="alert">{{ t('preferences.failed') }}</p>

      <div class="pp__footer">
        <button type="button" class="pp__save" :disabled="saving" @click="save">
          <Icon v-if="saving" icon="mdi:loading" width="14" height="14" class="pp__spin" />
          {{ t('preferences.save') }}
        </button>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.pp__backdrop {
  position: fixed;
  inset: 0;
  z-index: 1100;
  background: rgba(0, 0, 0, 0.35);
}

.pp {
  position: fixed;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  z-index: 1101;
  width: 420px;
  max-width: calc(100vw - 32px);
  max-height: calc(100vh - 32px);
  overflow-y: auto;
  padding: 14px 16px 16px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.pp__header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.pp__icon {
  color: var(--color-accent);
}

.pp__title {
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--color-text);
}

.pp__text,
.pp__hint {
  margin: 0;
  font-size: 0.8rem;
  color: var(--color-text-muted);
  line-height: 1.45;
}

.pp__hint {
  font-size: 0.75rem;
}

.pp__group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.pp__label {
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.pp__options {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px;
}

.pp__option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  padding: 8px 10px;
  font-family: inherit;
  font-size: 0.82rem;
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 6px;
  cursor: pointer;
  color: var(--color-text);
}

.pp__option:hover:not(:disabled) {
  border-color: var(--color-accent);
}

.pp__option--active {
  border-color: var(--color-accent);
  color: var(--color-accent);
}

.pp__option:disabled {
  opacity: 0.6;
  cursor: progress;
}

.pp__error {
  margin: 0;
  font-size: 0.75rem;
  color: #e05555;
}

.pp__footer {
  display: flex;
  justify-content: flex-end;
}

.pp__save {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 16px;
  font-family: inherit;
  font-size: 0.8125rem;
  font-weight: 500;
  border: 1px solid var(--color-accent);
  border-radius: 5px;
  background: var(--color-accent);
  color: #fff;
  cursor: pointer;
}

.pp__save:hover:not(:disabled) {
  opacity: 0.88;
}

.pp__save:disabled {
  opacity: 0.6;
  cursor: progress;
}

.pp__spin {
  animation: pp-spin 1s linear infinite;
}

@keyframes pp-spin {
  to { transform: rotate(360deg); }
}
</style>

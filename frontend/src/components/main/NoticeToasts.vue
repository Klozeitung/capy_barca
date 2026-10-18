<script setup lang="ts">
/**
 * NoticeToasts
 *
 * Shows the notices of the notices store in the lower right corner. Each one
 * closes on its own after a few seconds or when its close button is clicked.
 * Announced politely to screen readers.
 *
 * Also turns the API client's read-only event into a notice, so a change the
 * server refused because the user may only read the block does not vanish
 * without a word.
 */
import { onMounted, onBeforeUnmount } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { API_READ_ONLY_EVENT } from '@/api/client'
import { useNoticesStore, type Notice } from '@/stores/notices'

const { t } = useI18n()
const store = useNoticesStore()

/** i18n key of the notice shown when a change is refused as read-only. */
const READ_ONLY_NOTICE_KEY = 'notices.readOnly'

const ICONS: Record<string, string> = {
  [READ_ONLY_NOTICE_KEY]: 'mdi:lock-outline',
}
const DEFAULT_ICON = 'mdi:account-sync-outline'

function onReadOnly(): void {
  store.push(READ_ONLY_NOTICE_KEY)
}

onMounted(() => window.addEventListener(API_READ_ONLY_EVENT, onReadOnly))
onBeforeUnmount(() => window.removeEventListener(API_READ_ONLY_EVENT, onReadOnly))

/** The parameters of *notice*, with an empty entry title shown as untitled. */
function paramsOf(notice: Notice): Record<string, string> {
  if (!('entry' in notice.params) || notice.params.entry) return notice.params
  return { ...notice.params, entry: t('main.untitled') }
}
</script>

<template>
  <div class="notices" role="status" aria-live="polite">
    <div v-for="notice in store.notices" :key="notice.id" class="notices__item">
      <Icon :icon="ICONS[notice.key] ?? DEFAULT_ICON" width="16" height="16" class="notices__icon" />
      <span class="notices__text">{{ t(notice.key, paramsOf(notice)) }}</span>
      <button
        class="notices__close"
        :aria-label="t('notices.dismiss')"
        :title="t('notices.dismiss')"
        @click="store.dismiss(notice.id)"
      >
        <Icon icon="mdi:close" width="14" height="14" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.notices {
  position: fixed;
  right: 16px;
  bottom: 16px;
  z-index: 2000;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-width: min(420px, calc(100vw - 32px));
  pointer-events: none;
}

.notices__item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-bg);
  color: var(--color-text);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
  font-size: 0.8125rem;
  line-height: 1.4;
  pointer-events: auto;
}

.notices__icon {
  flex-shrink: 0;
  margin-top: 1px;
  color: var(--color-accent);
}

.notices__text {
  flex: 1;
}

.notices__close {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 2px;
  border: none;
  border-radius: 4px;
  background: none;
  color: var(--color-text-muted);
  cursor: pointer;
}

.notices__close:hover {
  background: var(--color-hover);
}

@media print {
  .notices {
    display: none;
  }
}
</style>

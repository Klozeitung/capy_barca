<script setup lang="ts">
/**
 * NoticeToasts
 *
 * Shows the notices of the notices store in the lower right corner. Each one
 * closes on its own after a few seconds or when its close button is clicked.
 * Announced politely to screen readers.
 */
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { useNoticesStore, type Notice } from '@/stores/notices'

const { t } = useI18n()
const store = useNoticesStore()

/** The parameters of *notice*, with an empty entry title shown as untitled. */
function paramsOf(notice: Notice): Record<string, string> {
  if (!('entry' in notice.params) || notice.params.entry) return notice.params
  return { ...notice.params, entry: t('main.untitled') }
}
</script>

<template>
  <div class="notices" role="status" aria-live="polite">
    <div v-for="notice in store.notices" :key="notice.id" class="notices__item">
      <Icon icon="mdi:account-sync-outline" width="16" height="16" class="notices__icon" />
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

<script setup lang="ts">
/**
 * ReadOnlyBadge
 *
 * Small lock badge telling the user that they may look at what is shown but
 * not change it. Rendered by the page views and by inline databases when
 * ``useEditable`` says so.
 */
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
</script>

<template>
  <span class="read-only-badge" :title="t('readOnly.badgeTitle')">
    <Icon icon="mdi:lock-outline" width="13" height="13" />
    <span>{{ t('readOnly.badge') }}</span>
  </span>
</template>

<style scoped>
.read-only-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: var(--color-surface);
  color: var(--color-text-muted);
  font-size: 0.75rem;
  line-height: 1.4;
  white-space: nowrap;
  user-select: none;
}

@media print {
  .read-only-badge {
    display: none;
  }
}
</style>

<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Icon } from '@iconify/vue'
import { useSettingsModal } from '@/composables/useSettingsModal'
import { useHelpModal } from '@/composables/useHelpModal'
import ProfileModal from './ProfileModal.vue'

const { t } = useI18n()
const { openSettings } = useSettingsModal()
const { openHelp } = useHelpModal()

const showProfile = ref(false)
</script>

<template>
  <div class="nav-admin">
    <button class="admin-item" @click="openSettings">
      <Icon icon="mdi:cog-outline" width="16" height="16" class="admin-item__icon" />
      <span>{{ t('nav.settings') }}</span>
    </button>
    <button class="admin-item" @click="openHelp">
      <Icon icon="mdi:help-circle-outline" width="16" height="16" class="admin-item__icon" />
      <span>{{ t('nav.help') }}</span>
    </button>
    <button class="admin-item" @click="showProfile = true">
      <Icon icon="mdi:account-outline" width="16" height="16" class="admin-item__icon" />
      <span>{{ t('nav.profile') }}</span>
    </button>
  </div>

  <ProfileModal v-if="showProfile" @close="showProfile = false" />
</template>

<style scoped>
.nav-admin {
  flex-shrink: 0;
  border-top: 1px solid var(--color-border);
  padding: 0.375rem 0.5rem;
}

.admin-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  height: 32px;
  padding: 0 8px;
  border: none;
  border-radius: 4px;
  background: none;
  cursor: pointer;
  font-size: 0.875rem;
  color: var(--color-text-muted);
  text-align: left;
  transition: background 0.1s, color 0.1s;
}

.admin-item:hover {
  background: var(--color-hover);
  color: var(--color-text);
}

.admin-item__icon {
  flex-shrink: 0;
}
</style>

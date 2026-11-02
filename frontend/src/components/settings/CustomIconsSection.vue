<script setup lang="ts">
/**
 * CustomIconsSection
 *
 * Content of the "Icons" tab of the global settings (admin only): the images
 * uploaded as icons. Every account can pick them in the icon picker; only
 * admins upload and delete them here.
 *
 * Deleting asks for confirmation with a second click, like deactivating a
 * user. The server replaces a deleted icon wherever it was used: entries get
 * their database's default icon, everything else loses it.
 */
import { computed, onMounted, ref } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { ApiError } from '@/api/client'
import { useCustomIconsStore } from '@/stores/customIcons'

const { t } = useI18n()
const store = useCustomIconsStore()

/** File types the server accepts, offered by the file dialog. */
const ACCEPT = 'image/png,image/jpeg,image/gif,image/webp,.png,.jpg,.jpeg,.gif,.webp'

const fileInput = ref<HTMLInputElement | null>(null)
const uploading = ref(false)
const feedback = ref<{ msg: string; ok: boolean } | null>(null)
const confirmDelete = ref<string | null>(null)
const deleting = ref<string | null>(null)

const icons = computed(() =>
  [...store.icons].sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true })),
)

onMounted(() => {
  void store.ensureLoaded()
})

function chooseFile(): void {
  feedback.value = null
  fileInput.value?.click()
}

function uploadError(error: unknown): string {
  if (error instanceof ApiError && error.status === 413) return t('settings.iconsTooLarge')
  if (error instanceof ApiError && error.status === 415) return t('settings.iconsWrongType')
  return t('settings.iconsUploadError')
}

async function onFilesChosen(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const files = [...(input.files ?? [])]
  input.value = ''
  if (files.length === 0) return
  uploading.value = true
  feedback.value = null
  let uploaded = 0
  let failure: string | null = null
  for (const file of files) {
    try {
      await store.upload(file)
      uploaded++
    } catch (error) {
      failure = uploadError(error)
    }
  }
  uploading.value = false
  feedback.value = failure
    ? { msg: failure, ok: false }
    : { msg: t('settings.iconsUploaded', { count: uploaded }, uploaded), ok: true }
}

async function requestDelete(id: string): Promise<void> {
  feedback.value = null
  if (confirmDelete.value !== id) {
    confirmDelete.value = id
    return
  }
  confirmDelete.value = null
  deleting.value = id
  try {
    await store.remove(id)
  } catch {
    feedback.value = { msg: t('settings.iconsDeleteError'), ok: false }
  } finally {
    deleting.value = null
  }
}
</script>

<template>
  <div class="icons">
    <p class="icons__desc">{{ t('settings.iconsDesc') }}</p>

    <div class="icons__actions">
      <button
        type="button"
        class="icons__btn icons__btn--primary"
        :disabled="uploading"
        @click="chooseFile"
      >
        <Icon
          :icon="uploading ? 'mdi:loading' : 'mdi:upload'"
          width="15"
          height="15"
          :class="{ icons__spin: uploading }"
        />
        {{ t('settings.iconsUpload') }}
      </button>
      <span class="icons__hint">{{ t('settings.iconsHint') }}</span>
      <input
        ref="fileInput"
        class="icons__file"
        type="file"
        multiple
        :accept="ACCEPT"
        @change="onFilesChosen"
      />
    </div>

    <p
      v-if="feedback"
      class="icons__feedback"
      :class="feedback.ok ? 'icons__feedback--ok' : 'icons__feedback--err'"
    >
      {{ feedback.msg }}
    </p>

    <p v-if="store.loaded && icons.length === 0" class="icons__empty">
      {{ t('settings.iconsNone') }}
    </p>

    <ul v-else class="icons__list">
      <li v-for="item in icons" :key="item.id" class="icons__row">
        <img class="icons__preview" :src="item.url" :alt="item.name" />
        <span class="icons__name" :title="item.name">{{ item.name }}</span>
        <button
          type="button"
          class="icons__delete"
          :class="{ 'icons__delete--confirm': confirmDelete === item.id }"
          :disabled="deleting === item.id"
          :title="confirmDelete === item.id ? t('settings.iconsDeleteConfirm') : t('settings.iconsDelete')"
          :aria-label="confirmDelete === item.id ? t('settings.iconsDeleteConfirm') : t('settings.iconsDelete')"
          @click="requestDelete(item.id)"
        >
          <Icon
            :icon="confirmDelete === item.id ? 'mdi:check' : 'mdi:trash-can-outline'"
            width="15"
            height="15"
          />
        </button>
      </li>
    </ul>

    <p v-if="icons.length > 0" class="icons__hint">{{ t('settings.iconsDeleteHint') }}</p>
  </div>
</template>

<style scoped>
.icons {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.icons__desc {
  margin: 0;
  font-size: 0.8125rem;
  color: var(--color-text-muted);
  line-height: 1.5;
}

.icons__actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}

.icons__file {
  display: none;
}

.icons__hint {
  margin: 0;
  font-size: 0.75rem;
  color: var(--color-text-muted);
  line-height: 1.45;
}

.icons__btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  font-size: 0.8125rem;
  font-family: inherit;
  font-weight: 500;
  border: 1px solid var(--color-border);
  border-radius: 5px;
  background: none;
  color: var(--color-text);
  cursor: pointer;
  white-space: nowrap;
}

.icons__btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.icons__btn--primary {
  border-color: var(--color-accent);
  background: var(--color-accent);
  color: #fff;
}

.icons__btn--primary:hover:not(:disabled) {
  opacity: 0.88;
}

.icons__feedback {
  margin: 0;
  font-size: 0.8125rem;
}

.icons__feedback--ok  { color: #4caf7d; }
.icons__feedback--err { color: #e05353; }

.icons__empty {
  margin: 0;
  font-size: 0.8125rem;
  color: var(--color-text-muted);
}

.icons__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 6px;
}

.icons__row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  padding: 6px 8px;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-bg);
}

.icons__preview {
  width: 24px;
  height: 24px;
  object-fit: contain;
  flex-shrink: 0;
}

.icons__name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 0.8125rem;
  color: var(--color-text);
}

.icons__delete {
  display: flex;
  align-items: center;
  flex-shrink: 0;
  padding: 3px;
  border: none;
  border-radius: 4px;
  background: none;
  color: var(--color-text-muted);
  cursor: pointer;
}

.icons__delete:hover:not(:disabled) {
  background: var(--color-hover);
  color: var(--color-text);
}

.icons__delete--confirm {
  color: #e05353;
}

.icons__delete:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.icons__spin {
  animation: icons-spin 1s linear infinite;
}

@keyframes icons-spin {
  to { transform: rotate(360deg); }
}
</style>

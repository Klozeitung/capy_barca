<script setup lang="ts">
/**
 * AudioBlock
 *
 * Upload drop zone for audio files. Renders a native <audio> element with
 * controls once a file is stored. Content shape: { file_uuid, url, filename,
 * size, mime }.
 *
 * Without write access to the block (``useEditable``) an empty block shows a
 * plain placeholder instead of the upload zone, and there is no remove button.
 */
import { computed, ref } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { useBlockStore, type Block } from '@/stores/blocks'
import { useMediaUpload } from '@/composables/useMediaUpload'
import { provideEditable } from '@/composables/useEditable'

const props = defineProps<{
  block: Block
  parentId: string
}>()

const { t } = useI18n()
const blockStore = useBlockStore()
const editable = provideEditable(() => props.block)
const upload = useMediaUpload('audio', props.block.id)
const inputRef = ref<HTMLInputElement | null>(null)

const hasFile = computed(() => Boolean(props.block.content?.file_uuid))
const fileUrl = computed(() => props.block.content?.url as string | undefined)
const filename = computed(() => props.block.content?.filename as string | undefined)
const mime = computed(() => props.block.content?.mime as string | undefined)

async function handleDrop(e: DragEvent): Promise<void> {
  const f = await upload.onDrop(e)
  if (f) await blockStore.updateBlock(props.block.id, { content: f })
}

async function handleSelect(e: Event): Promise<void> {
  const f = await upload.onFileSelect(e)
  if (f) await blockStore.updateBlock(props.block.id, { content: f })
}

async function handleRemove(): Promise<void> {
  const uuid = props.block.content?.file_uuid as string | undefined
  if (uuid) await upload.deleteFile(uuid)
  await blockStore.updateBlock(props.block.id, { content: {} })
}
</script>

<template>
  <div class="media-block">
    <template v-if="!hasFile && editable">
      <div
        class="media-block__dropzone"
        :class="{ 'media-block__dropzone--over': upload.isDragging.value }"
        @dragover.prevent.stop="upload.onDragEnter"
        @dragleave.stop="upload.onDragLeave"
        @drop.prevent.stop="handleDrop"
        @click="inputRef?.click()"
      >
        <Icon icon="mdi:music-note-plus" width="28" height="28" class="media-block__zone-icon" />
        <span class="media-block__zone-hint">
          <template v-if="upload.isUploading.value">{{ t('block.media.uploading') }}</template>
          <template v-else>{{ t('block.media.dropAudio') }}</template>
        </span>
        <span v-if="upload.error.value" class="media-block__zone-error">
          {{ upload.error.value }}
        </span>
      </div>
      <input
        ref="inputRef"
        type="file"
        accept="audio/*"
        class="media-block__file-input"
        @change="handleSelect"
      />
    </template>

    <div v-else-if="!hasFile" class="media-block__dropzone media-block__dropzone--static">
      <Icon icon="mdi:music-note-plus" width="28" height="28" class="media-block__zone-icon" />
      <span class="media-block__zone-hint">{{ t('block.media.empty') }}</span>
    </div>

    <template v-else>
      <div class="media-block__audio-card">
        <span class="media-block__audio-icon">
          <Icon icon="mdi:music-note" width="20" height="20" />
        </span>
        <div class="media-block__audio-body">
          <span class="media-block__audio-name">{{ filename }}</span>
          <audio controls class="media-block__audio-player">
            <source :src="fileUrl" :type="mime" />
          </audio>
        </div>
        <button
          v-if="editable"
          class="media-block__remove-btn"
          :title="t('block.media.removeAudio')"
          @click="handleRemove"
        >
          <Icon icon="mdi:close" width="14" height="14" />
        </button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.media-block {
  flex: 1;
  min-width: 0;
}

.media-block__dropzone {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 24px 16px;
  border: 1.5px dashed var(--color-border);
  border-radius: 8px;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
  user-select: none;
}

.media-block__dropzone:hover,
.media-block__dropzone--over {
  border-color: var(--color-accent);
  background: var(--color-accent-subtle, color-mix(in srgb, var(--color-accent) 8%, transparent));
}

.media-block__zone-icon {
  color: var(--color-text-muted);
}

.media-block__zone-hint {
  font-size: 0.8125rem;
  color: var(--color-text-muted);
}

.media-block__zone-error {
  font-size: 0.75rem;
  color: #e05353;
}

.media-block__file-input {
  display: none;
}

/* ── Audio card ──────────────────────────────────────────────────────────── */
.media-block__audio-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: var(--color-surface);
  position: relative;
}

.media-block__audio-icon {
  flex-shrink: 0;
  color: var(--color-accent);
  display: flex;
  align-items: center;
}

.media-block__audio-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.media-block__audio-name {
  font-size: 0.8125rem;
  color: var(--color-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.media-block__audio-player {
  width: 100%;
  height: 32px;
}

.media-block__remove-btn {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  padding: 0;
  border: none;
  border-radius: 4px;
  background: var(--color-hover);
  color: var(--color-text-muted);
  cursor: pointer;
  transition: background 0.1s, color 0.1s;
}

.media-block__remove-btn:hover {
  background: var(--color-active);
  color: var(--color-text);
}

/* ── Read-only placeholder (after the drop zone rules so these win) ─────── */
.media-block__dropzone--static,
.media-block__dropzone--static:hover {
  cursor: default;
  border-color: var(--color-border);
  background: transparent;
}
</style>

<script setup lang="ts">
/**
 * ImageBlock
 *
 * Renders an image upload drop zone when empty, or a full-width image preview
 * once a file has been uploaded. The block content stores the UploadedFile
 * fields directly: { file_uuid, url, filename, size, mime }.
 */
import { computed, ref } from 'vue'
import { Icon } from '@iconify/vue'
import { useBlockStore, type Block } from '@/stores/blocks'
import { useMediaUpload } from '@/composables/useMediaUpload'

const props = defineProps<{
  block: Block
  parentId: string
}>()

const blockStore = useBlockStore()
const upload = useMediaUpload('image', props.block.id)
const inputRef = ref<HTMLInputElement | null>(null)

const hasFile = computed(() => Boolean(props.block.content?.file_uuid))
const fileUrl = computed(() => props.block.content?.url as string | undefined)
const filename = computed(() => props.block.content?.filename as string | undefined)

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
    <template v-if="!hasFile">
      <div
        class="media-block__dropzone"
        :class="{ 'media-block__dropzone--over': upload.isDragging.value }"
        @dragover.prevent.stop="upload.onDragEnter"
        @dragleave.stop="upload.onDragLeave"
        @drop.prevent.stop="handleDrop"
        @click="inputRef?.click()"
      >
        <Icon icon="mdi:image-plus-outline" width="28" height="28" class="media-block__zone-icon" />
        <span class="media-block__zone-hint">
          <template v-if="upload.isUploading.value">Uploading…</template>
          <template v-else>Drop an image, or click to upload</template>
        </span>
        <span v-if="upload.error.value" class="media-block__zone-error">
          {{ upload.error.value }}
        </span>
      </div>
      <input
        ref="inputRef"
        type="file"
        accept="image/*"
        class="media-block__file-input"
        @change="handleSelect"
      />
    </template>

    <template v-else>
      <div class="media-block__preview media-block__preview--image">
        <img :src="fileUrl" :alt="filename" class="media-block__image" />
        <button class="media-block__remove-btn" title="Remove image" @click="handleRemove">
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

/* ── Drop zone ───────────────────────────────────────────────────────────── */
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
  background: transparent;
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

/* ── Preview ─────────────────────────────────────────────────────────────── */
.media-block__preview {
  position: relative;
  display: inline-block;
  max-width: 100%;
  border-radius: 6px;
  overflow: hidden;
}

.media-block__image {
  display: block;
  max-width: 100%;
  max-height: 480px;
  object-fit: contain;
  border-radius: 6px;
}

.media-block__remove-btn {
  position: absolute;
  top: 6px;
  right: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  padding: 0;
  border: none;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.5);
  color: #fff;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s;
}

.media-block__preview:hover .media-block__remove-btn {
  opacity: 1;
}
</style>

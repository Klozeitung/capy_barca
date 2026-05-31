<script setup lang="ts">
/**
 * FileBlock
 *
 * Accepts a single file of any type via drop or click-select. Renders a
 * compact download card once uploaded. Content shape:
 * { file_uuid, url, filename, size, mime }.
 */
import { computed, ref } from 'vue'
import { Icon } from '@iconify/vue'
import { useBlockStore, type Block } from '@/stores/blocks'
import { useMediaUpload } from '@/composables/useMediaUpload'
import { downloadFile } from '@/composables/useDownload'

const props = defineProps<{
  block: Block
  parentId: string
}>()

const blockStore = useBlockStore()
const upload = useMediaUpload('file', props.block.id)
const inputRef = ref<HTMLInputElement | null>(null)

const hasFile = computed(() => Boolean(props.block.content?.file_uuid))
const fileUrl = computed(() => props.block.content?.url as string | undefined)
const filename = computed(() => props.block.content?.filename as string | undefined)
const size = computed(() => props.block.content?.size as number | undefined)
const mime = computed(() => props.block.content?.mime as string | undefined)

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function iconForMime(m: string): string {
  if (m.startsWith('image/')) return 'mdi:image-outline'
  if (m.startsWith('video/')) return 'mdi:video-outline'
  if (m.startsWith('audio/')) return 'mdi:music-note-outline'
  if (m === 'application/pdf') return 'mdi:file-pdf-box'
  if (m.startsWith('text/')) return 'mdi:file-document-outline'
  if (m.includes('zip') || m.includes('tar') || m.includes('gzip')) return 'mdi:zip-box-outline'
  return 'mdi:file-outline'
}

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

function handleDownload(): void {
  if (fileUrl.value && filename.value) {
    downloadFile(fileUrl.value, filename.value)
  }
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
        <Icon icon="mdi:file-upload-outline" width="28" height="28" class="media-block__zone-icon" />
        <span class="media-block__zone-hint">
          <template v-if="upload.isUploading.value">Uploading…</template>
          <template v-else>Drop a file, or click to upload</template>
        </span>
        <span v-if="upload.error.value" class="media-block__zone-error">
          {{ upload.error.value }}
        </span>
      </div>
      <input
        ref="inputRef"
        type="file"
        class="media-block__file-input"
        @change="handleSelect"
      />
    </template>

    <template v-else>
      <div class="file-card">
        <span class="file-card__icon">
          <Icon :icon="iconForMime(mime ?? '')" width="22" height="22" />
        </span>
        <div class="file-card__info">
          <span class="file-card__name">{{ filename }}</span>
          <span class="file-card__meta">{{ size !== undefined ? formatSize(size) : '' }}</span>
        </div>
        <button
          class="file-card__download"
          title="Download"
          @click="handleDownload"
        >
          <Icon icon="mdi:download" width="16" height="16" />
        </button>
        <button class="file-card__remove" title="Remove file" @click="handleRemove">
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

/* ── File card ───────────────────────────────────────────────────────────── */
.file-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: var(--color-surface);
  transition: background 0.1s;
}

.file-card:hover {
  background: var(--color-hover);
}

.file-card__icon {
  flex-shrink: 0;
  color: var(--color-accent);
  display: flex;
  align-items: center;
}

.file-card__info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.file-card__name {
  font-size: 0.875rem;
  font-weight: 500;
  color: var(--color-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.file-card__meta {
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

.file-card__download,
.file-card__remove {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--color-text-muted);
  cursor: pointer;
  text-decoration: none;
  transition: background 0.1s, color 0.1s;
}

.file-card__download:hover,
.file-card__remove:hover {
  background: var(--color-active);
  color: var(--color-text);
}
</style>

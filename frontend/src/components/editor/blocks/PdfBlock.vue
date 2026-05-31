<script setup lang="ts">
/**
 * PdfBlock
 *
 * Upload drop zone for PDF files. Renders the PDF inside an <iframe> using
 * the browser's built-in PDF viewer once uploaded. Content shape:
 * { file_uuid, url, filename, size, mime }.
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
const upload = useMediaUpload('pdf', props.block.id)
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
        <Icon icon="mdi:file-pdf-box" width="28" height="28" class="media-block__zone-icon" />
        <span class="media-block__zone-hint">
          <template v-if="upload.isUploading.value">Uploading…</template>
          <template v-else>Drop a PDF, or click to upload</template>
        </span>
        <span v-if="upload.error.value" class="media-block__zone-error">
          {{ upload.error.value }}
        </span>
      </div>
      <input
        ref="inputRef"
        type="file"
        accept="application/pdf"
        class="media-block__file-input"
        @change="handleSelect"
      />
    </template>

    <template v-else>
      <div class="media-block__pdf-wrapper">
        <div class="media-block__pdf-toolbar">
          <span class="media-block__pdf-name">
            <Icon icon="mdi:file-pdf-box" width="14" height="14" />
            {{ filename }}
          </span>
          <a
            :href="fileUrl"
            target="_blank"
            rel="noopener noreferrer"
            class="media-block__pdf-open"
            title="Open in new tab"
          >
            <Icon icon="mdi:open-in-new" width="14" height="14" />
          </a>
          <button class="media-block__pdf-remove" title="Remove PDF" @click="handleRemove">
            <Icon icon="mdi:close" width="14" height="14" />
          </button>
        </div>
        <iframe
          :src="fileUrl"
          class="media-block__pdf-frame"
          :title="filename"
        />
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

/* ── PDF viewer ──────────────────────────────────────────────────────────── */
.media-block__pdf-wrapper {
  border: 1px solid var(--color-border);
  border-radius: 8px;
  overflow: hidden;
}

.media-block__pdf-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  background: var(--color-surface);
  border-bottom: 1px solid var(--color-border);
}

.media-block__pdf-name {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 0.8125rem;
  color: var(--color-text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.media-block__pdf-open,
.media-block__pdf-remove {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  padding: 0;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--color-text-muted);
  cursor: pointer;
  text-decoration: none;
  transition: background 0.1s, color 0.1s;
}

.media-block__pdf-open:hover,
.media-block__pdf-remove:hover {
  background: var(--color-hover);
  color: var(--color-text);
}

.media-block__pdf-frame {
  display: block;
  width: 100%;
  height: 600px;
  border: none;
  background: var(--color-bg);
}
</style>

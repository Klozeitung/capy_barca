<script setup lang="ts">
/**
 * FileCell
 *
 * Renders uploaded files as clickable name chips. When isActive a hidden
 * file-input label is shown so additional files can be appended, and each
 * chip gains a remove button.
 *
 * Uploads go to /api/media/upload/file/{entryId}; deletes to
 * /api/media/file/{entryId}/{fileUuid}.
 *
 * Value shape: { files: StoredFile[] }
 *
 * Without write access (``useEditable``) the files are listed and open, but
 * the cell does not activate, so nothing can be added or removed.
 *
 * Uploads and deletions go through ``apiClient``, so an expired session and a
 * refusal for read-only access are handled as everywhere else.
 */
import { Icon } from '@iconify/vue'
import { apiClient, ApiError } from '@/api/client'
import { useEditable } from '@/composables/useEditable'
import { useDatabaseStore, type DatabaseEntry, type PropertySchema } from '@/stores/database'
import { getCellValue } from './cellUtils'

// ── Types ─────────────────────────────────────────────────────────────────────

interface StoredFile {
  file_uuid: string
  url: string
  name: string
  size: number
  mime: string
}

// ── Props / emits ─────────────────────────────────────────────────────────────

const props = defineProps<{
  entry: DatabaseEntry
  schema: PropertySchema
  databaseId: string
  isActive: boolean
}>()

const emit = defineEmits<{
  activate: []
  deactivate: []
}>()

const dbStore = useDatabaseStore()
const editable = useEditable()

/** Ask the parent to open this cell for editing, with write access only. */
function activate(): void {
  if (editable.value) emit('activate')
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fileList(): StoredFile[] {
  const val = getCellValue(props.entry, props.schema.id)
  return (val?.files as StoredFile[] | undefined) ?? []
}

// ── Upload ────────────────────────────────────────────────────────────────────

async function handleUpload(event: Event) {
  const input = event.target as HTMLInputElement
  if (!input.files?.length) return
  const existing = fileList()
  const uploaded: StoredFile[] = []

  for (const file of Array.from(input.files)) {
    const form = new FormData()
    form.append('file', file)
    try {
      const data = await apiClient.postForm<{
        file_uuid: string
        url: string
        filename: string
        size: number
        mime: string
      }>(`/api/media/upload/file/${props.entry.id}`, form)
      uploaded.push({
        file_uuid: data.file_uuid,
        url: data.url,
        name: data.filename,
        size: data.size,
        mime: data.mime,
      })
    } catch {
      // Individual upload failures are silently skipped; existing files are preserved.
    }
  }

  if (uploaded.length > 0) {
    // upsertValue patches every cached copy of the entry in place.
    await dbStore.upsertValue(props.databaseId, props.entry.id, props.schema.id, {
      files: [...existing, ...uploaded],
    })
  }

  // Reset so the same file can be re-selected immediately.
  input.value = ''
}

// ── Remove ────────────────────────────────────────────────────────────────────

async function removeFile(fileUuid: string) {
  const current = fileList()
  const updated = current.filter(f => f.file_uuid !== fileUuid)

  try {
    await apiClient.delete(`/api/media/file/${props.entry.id}/${fileUuid}`)
  } catch (e) {
    // A refusal by the server (for instance a file that is already gone) does
    // not keep the file in the list; only a network failure stops here.
    if (!(e instanceof ApiError) || e.status === 0) throw e
  }

  await dbStore.upsertValue(
    props.databaseId,
    props.entry.id,
    props.schema.id,
    updated.length > 0 ? { files: updated } : null,
  )
}
</script>

<template>
  <div
    class="db__file-cell"
    :class="{ 'db__file-cell--active': isActive }"
    @click.stop="activate"
  >
    <div class="db__file-chips">
      <span
        v-for="f in fileList()"
        :key="f.file_uuid"
        class="db__file-chip"
      >
        <a
          :href="f.url"
          target="_blank"
          rel="noopener noreferrer"
          class="db__file-chip-name"
          @click.stop
        >
          {{ f.name }}
        </a>
        <button
          v-if="isActive"
          class="db__file-chip-remove"
          @click.stop="removeFile(f.file_uuid)"
        >
          <Icon icon="mdi:close" width="10" height="10" />
        </button>
      </span>
    </div>

    <label v-if="isActive" class="db__file-add-btn" @click.stop>
      <Icon icon="mdi:plus" width="12" height="12" />
      <input
        type="file"
        multiple
        class="db__file-input-hidden"
        @change="handleUpload"
      />
    </label>
  </div>
</template>

<style scoped>
.db__file-cell {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  padding: 5px 8px;
  min-height: 36px;
  cursor: pointer;
}

.db__file-cell--active {
  outline: 2px solid var(--color-accent);
  outline-offset: -2px;
}

.db__file-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 3px;
  align-items: center;
}

.db__file-chip {
  display: flex;
  align-items: center;
  gap: 3px;
  background: var(--color-accent-subtle);
  border: 1px solid var(--color-accent);
  border-radius: 3px;
  padding: 1px 5px;
  font-size: 0.73rem;
}

.db__file-chip-name {
  color: var(--color-text);
  text-decoration: none;
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.db__file-chip-name:hover {
  text-decoration: underline;
}

.db__file-chip-remove {
  background: transparent;
  border: none;
  cursor: pointer;
  color: var(--color-text-muted);
  display: flex;
  align-items: center;
  padding: 1px;
  border-radius: 2px;
  flex-shrink: 0;
}

.db__file-chip-remove:hover {
  color: #e05555;
}

.db__file-add-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border: 1px dashed var(--color-border);
  border-radius: 3px;
  cursor: pointer;
  color: var(--color-text-muted);
  transition: border-color 0.15s, color 0.15s;
  flex-shrink: 0;
}

.db__file-add-btn:hover {
  border-color: var(--color-accent);
  color: var(--color-accent);
}

.db__file-input-hidden {
  display: none;
}
</style>

/**
 * useMediaUpload
 *
 * Shared composable for all media/file block types (image, video, audio, pdf,
 * file, drive). Manages drag state, upload progress, and error feedback.
 *
 * All async functions return values rather than using callbacks so callers can
 * act on results with plain ``await`` in their event handlers.
 *
 * HTTP 413 (Request Entity Too Large) is surfaced as a dedicated, localised
 * error message so the block dropzones can display it as a tooltip.
 */
import { ref } from 'vue'

export type MediaCategory = 'image' | 'video' | 'audio' | 'pdf' | 'file' | 'drive'

export interface UploadedFile {
  file_uuid: string
  url: string
  filename: string
  size: number
  mime: string
  [key: string]: unknown
}

export function useMediaUpload(category: MediaCategory, blockId: string) {
  const isDragging = ref(false)
  const isUploading = ref(false)
  const error = ref<string | null>(null)

  // ── Core upload ────────────────────────────────────────────────────────────

  async function uploadFile(file: File): Promise<UploadedFile | null> {
    isUploading.value = true
    error.value = null
    try {
      const form = new FormData()
      form.append('file', file)
      const res = await fetch(`/api/media/upload/${category}/${blockId}`, {
        method: 'POST',
        body: form,
        credentials: 'include',
      })
      if (!res.ok) {
        if (res.status === 413) {
          throw new Error(
            'Die Datei, die du versucht hast, hochzuladen, übersteigt die verbliebene Kapazität des Servers.',
          )
        }
        throw new Error(`Upload fehlgeschlagen (${res.status})`)
      }
      return (await res.json()) as UploadedFile
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Upload fehlgeschlagen'
      return null
    } finally {
      isUploading.value = false
    }
  }

  async function uploadMultiple(files: FileList | File[]): Promise<UploadedFile[]> {
    const results: UploadedFile[] = []
    for (const file of Array.from(files)) {
      const r = await uploadFile(file)
      if (r) results.push(r)
    }
    return results
  }

  // ── Delete ─────────────────────────────────────────────────────────────────

  async function deleteFile(fileUuid: string): Promise<void> {
    await fetch(`/api/media/${category}/${blockId}/${fileUuid}`, {
      method: 'DELETE',
      credentials: 'include',
    })
  }

  // ── Drag state helpers (used as raw event handlers in templates) ───────────

  function onDragEnter(): void {
    isDragging.value = true
  }

  function onDragLeave(): void {
    isDragging.value = false
  }

  // ── Drop and select handlers ───────────────────────────────────────────────

  /** Handle a single-file drop. Returns the uploaded file or null. */
  async function onDrop(e: DragEvent): Promise<UploadedFile | null> {
    isDragging.value = false
    const file = e.dataTransfer?.files[0]
    if (!file) return null
    return await uploadFile(file)
  }

  /** Handle a multi-file drop. Returns all successfully uploaded files. */
  async function onDropMultiple(e: DragEvent): Promise<UploadedFile[]> {
    isDragging.value = false
    const files = e.dataTransfer?.files
    if (!files?.length) return []
    return await uploadMultiple(files)
  }

  /** Handle a single-file <input type="file"> change event. */
  async function onFileSelect(e: Event): Promise<UploadedFile | null> {
    const input = e.target as HTMLInputElement
    const file = input.files?.[0]
    input.value = ''
    if (!file) return null
    return await uploadFile(file)
  }

  /** Handle a multi-file <input type="file" multiple> change event. */
  async function onFileSelectMultiple(e: Event): Promise<UploadedFile[]> {
    const input = e.target as HTMLInputElement
    const files = input.files
    input.value = ''
    if (!files?.length) return []
    return await uploadMultiple(files)
  }

  return {
    isDragging,
    isUploading,
    error,
    uploadFile,
    deleteFile,
    onDragEnter,
    onDragLeave,
    onDrop,
    onDropMultiple,
    onFileSelect,
    onFileSelectMultiple,
  }
}

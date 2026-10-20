/**
 * useMediaUpload
 *
 * Shared composable for all media/file block types (image, video, audio, pdf,
 * file, drive). Manages drag state, upload progress, and error feedback.
 *
 * All async functions return values rather than using callbacks so callers can
 * act on results with plain ``await`` in their event handlers.
 *
 * Rejections the server can give are surfaced as their own localised messages
 * so the block dropzones can display them as a tooltip: 413 when the file is
 * past the size ceiling, 415 when the block does not accept that type, 403
 * when the block is not the caller's to write to.
 *
 * Requests go through ``apiClient``, so an expired session and a refusal for
 * read-only access are handled the same way as everywhere else.
 */
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { apiClient, ApiError } from '@/api/client'

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
  const { t } = useI18n()
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
      return await apiClient.postForm<UploadedFile>(
        `/api/media/upload/${category}/${blockId}`,
        form,
      )
    } catch (e) {
      error.value = uploadErrorMessage(e)
      return null
    } finally {
      isUploading.value = false
    }
  }

  function uploadErrorMessage(e: unknown): string {
    const status = e instanceof ApiError ? e.status : null
    if (status === 413) return t('errors.uploadTooLarge')
    if (status === 415) return t('errors.uploadTypeNotAllowed')
    if (status === 403) return t('errors.uploadForbidden')
    return t('errors.uploadFailed')
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

  /**
   * Delete a stored file. A refusal by the server (for instance a file that
   * is already gone) does not stop the caller from clearing the block; only
   * a network failure is passed on.
   */
  async function deleteFile(fileUuid: string): Promise<void> {
    try {
      await apiClient.delete(`/api/media/${category}/${blockId}/${fileUuid}`)
    } catch (e) {
      if (e instanceof ApiError && e.status !== 0) return
      throw e
    }
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

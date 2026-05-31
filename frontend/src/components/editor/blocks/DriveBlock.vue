<script setup lang="ts">
/**
 * DriveBlock
 *
 * A hierarchical file-storage block with folder navigation.
 *
 * Content shape:
 *   {
 *     title?:   string
 *     files:    UploadedFile[]
 *     folders:  DriveFolder[]
 *   }
 *
 * DriveFolder:
 *   { id: string, name: string, files: UploadedFile[], folders: DriveFolder[] }
 *
 * Folder structure is stored purely in the content JSON. Files are stored flat
 * on disk (static/uploads/drives/<block_id>/<uuid><ext>) regardless of their
 * logical folder position; folders are a purely UI concept.
 *
 * Features
 * --------
 * Folders     (#new) – Create folders, navigate into them, back arrow + breadcrumb.
 *                      Folder names editable by double-click.
 * Block title  (#30) – Double-click on header title to edit. Stored in content.title.
 * Block icon   (#30) – Click icon button to open IconPicker.
 * File rename  (#29) – Double-click on filename for inline edit.
 * File reorder (#31) – Drag handle on each row.
 * Collabora          – Office documents open in WOPI modal iframe.
 * Download fix       – fetch→blob→objectURL to guarantee correct filename.
 * Breadcrumb DnD(#37)– Breadcrumb path segments are drop targets; dragging a file
 *                      onto a crumb moves it to that ancestor folder.
 * Search       (#38) – Search icon in header toggles a search input; results are
 *                      a flat filtered list across all nested folders.
 */
import { computed, nextTick, ref, watch } from 'vue'
import { Icon } from '@iconify/vue'
import IconPicker from '@/components/IconPicker.vue'
import { useBlockStore, type Block } from '@/stores/blocks'
import { useMediaUpload, type UploadedFile } from '@/composables/useMediaUpload'
import { downloadFile } from '@/composables/useDownload'

// ── Types ─────────────────────────────────────────────────────────────────────

interface DriveFolder {
  id: string
  name: string
  files: UploadedFile[]
  folders: DriveFolder[]
}

interface SearchResult {
  file: UploadedFile
  /** Display path: folder names from root to the containing folder. */
  folderPath: string[]
}

// ── Props ─────────────────────────────────────────────────────────────────────

const props = defineProps<{
  block: Block
  parentId: string
}>()

// ── Store / upload ────────────────────────────────────────────────────────────

const blockStore = useBlockStore()
const upload = useMediaUpload('drive', props.block.id)
const inputRef = ref<HTMLInputElement | null>(null)
const isDropOver = ref(false)

// ── Tree helpers ──────────────────────────────────────────────────────────────

/**
 * The block content is the implicit root node.
 * We wrap it in a DriveFolder shape so all tree operations are uniform.
 */
const rootFolder = computed<DriveFolder>(() => ({
  id: '__root__',
  name: (props.block.content?.title as string | undefined) ?? '',
  files: (props.block.content?.files as UploadedFile[] | undefined) ?? [],
  folders: (props.block.content?.folders as DriveFolder[] | undefined) ?? [],
}))

/** Walk to the node identified by path (array of folder IDs). */
function nodeAtPath(root: DriveFolder, path: string[]): DriveFolder {
  let node = root
  for (const id of path) {
    const child = node.folders.find((f) => f.id === id)
    if (!child) return node // path broken – stay at current
    node = child
  }
  return node
}

/**
 * Immutable update: replace the node at path with updater(node).
 * Returns a new root with the minimal diff.
 */
function updateAtPath(
  node: DriveFolder,
  path: string[],
  updater: (n: DriveFolder) => DriveFolder,
): DriveFolder {
  if (path.length === 0) return updater(node)
  const [head, ...tail] = path
  return {
    ...node,
    folders: node.folders.map((f) =>
      f.id === head ? updateAtPath(f, tail, updater) : f,
    ),
  }
}

/**
 * Apply updater to the current folder and persist the result.
 * Only files/folders at the root level land in content.files / content.folders.
 */
async function persistUpdate(updater: (n: DriveFolder) => DriveFolder): Promise<void> {
  const newRoot = updateAtPath(rootFolder.value, currentPath.value, updater)
  await blockStore.updateBlock(props.block.id, {
    content: {
      ...props.block.content,
      files: newRoot.files,
      folders: newRoot.folders,
    },
  })
}

// ── Navigation ────────────────────────────────────────────────────────────────

/** Stack of folder IDs from root to current location. */
const currentPath = ref<string[]>([])

const currentFolder = computed(() => nodeAtPath(rootFolder.value, currentPath.value))

const breadcrumb = computed(() => {
  const crumbs: { id: string; name: string }[] = [
    { id: '__root__', name: displayTitle.value },
  ]
  let node = rootFolder.value
  for (const id of currentPath.value) {
    const child = node.folders.find((f) => f.id === id)
    if (!child) break
    crumbs.push({ id: child.id, name: child.name })
    node = child
  }
  return crumbs
})

function navigateInto(folder: DriveFolder): void {
  currentPath.value = [...currentPath.value, folder.id]
}

function navigateBack(): void {
  currentPath.value = currentPath.value.slice(0, -1)
}

function navigateTo(index: number): void {
  currentPath.value = currentPath.value.slice(0, index)
}

// ── Block title (#30) ─────────────────────────────────────────────────────────

const currentTitle = computed(() => (props.block.content?.title as string | undefined) ?? '')
const displayTitle = computed(() => currentTitle.value || 'Drive')

const editingTitle = ref(false)
const titleInput = ref(currentTitle.value)
const titleInputRef = ref<HTMLInputElement | null>(null)

watch(
  () => props.block.content?.title,
  (v) => { if (!editingTitle.value) titleInput.value = (v as string | undefined) ?? '' },
)

async function startTitleEdit(): Promise<void> {
  titleInput.value = currentTitle.value
  editingTitle.value = true
  await nextTick()
  titleInputRef.value?.select()
}

async function saveTitle(): Promise<void> {
  editingTitle.value = false
  const trimmed = titleInput.value.trim()
  if (trimmed === currentTitle.value) return
  await blockStore.updateBlock(props.block.id, {
    content: { ...props.block.content, title: trimmed || undefined },
  })
}

function onTitleKeydown(e: KeyboardEvent): void {
  if (e.key === 'Enter') { e.preventDefault(); saveTitle() }
  if (e.key === 'Escape') { editingTitle.value = false }
}

// ── Block icon (#30) ──────────────────────────────────────────────────────────

const displayIcon = computed(() => props.block.icon ?? 'mdi:folder-outline')
const showIconPicker = ref(false)

async function onIconUpdate(newIcon: string | null): Promise<void> {
  showIconPicker.value = false
  if (!newIcon || newIcon === props.block.icon) return
  await blockStore.updateAppearance(props.block.id, { icon: newIcon })
}

// ── Folder create ─────────────────────────────────────────────────────────────

const creatingFolder = ref(false)
const newFolderName = ref('')
const newFolderInputRef = ref<HTMLInputElement | null>(null)

async function startCreateFolder(): Promise<void> {
  creatingFolder.value = true
  newFolderName.value = ''
  await nextTick()
  newFolderInputRef.value?.focus()
}

async function confirmCreateFolder(): Promise<void> {
  creatingFolder.value = false
  const name = newFolderName.value.trim()
  if (!name) return
  const newFolder: DriveFolder = {
    id: `f-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    name,
    files: [],
    folders: [],
  }
  await persistUpdate((node) => ({
    ...node,
    folders: [...node.folders, newFolder],
  }))
}

function cancelCreateFolder(): void {
  creatingFolder.value = false
}

function onNewFolderKeydown(e: KeyboardEvent): void {
  if (e.key === 'Enter') { e.preventDefault(); confirmCreateFolder() }
  if (e.key === 'Escape') { cancelCreateFolder() }
}

// ── Folder rename ─────────────────────────────────────────────────────────────

const renamingFolderId = ref<string | null>(null)
const folderRenameValue = ref('')
const folderRenameInputRef = ref<HTMLInputElement | null>(null)

async function startFolderRename(folder: DriveFolder): Promise<void> {
  renamingFolderId.value = folder.id
  folderRenameValue.value = folder.name
  await nextTick()
  folderRenameInputRef.value?.select()
}

async function saveFolderRename(): Promise<void> {
  const id = renamingFolderId.value
  renamingFolderId.value = null
  if (!id) return
  const name = folderRenameValue.value.trim()
  if (!name) return
  await persistUpdate((node) => ({
    ...node,
    folders: node.folders.map((f) => (f.id === id ? { ...f, name } : f)),
  }))
}

function onFolderRenameKeydown(e: KeyboardEvent): void {
  if (e.key === 'Enter') { e.preventDefault(); saveFolderRename() }
  if (e.key === 'Escape') { renamingFolderId.value = null }
}

async function removeFolder(folderId: string): Promise<void> {
  await persistUpdate((node) => ({
    ...node,
    folders: node.folders.filter((f) => f.id !== folderId),
  }))
}

// ── File rename (#29) ─────────────────────────────────────────────────────────

const renamingFileUuid = ref<string | null>(null)
const renameValue = ref('')
const renameInputRef = ref<HTMLInputElement | null>(null)

async function startRename(file: UploadedFile): Promise<void> {
  renamingFileUuid.value = file.file_uuid
  renameValue.value = file.filename
  await nextTick()
  renameInputRef.value?.select()
}

async function saveRename(): Promise<void> {
  const uuid = renamingFileUuid.value
  renamingFileUuid.value = null
  if (!uuid) return
  const trimmed = renameValue.value.trim()
  if (!trimmed) return
  await persistUpdate((node) => ({
    ...node,
    files: node.files.map((f) => (f.file_uuid === uuid ? { ...f, filename: trimmed } : f)),
  }))
}

function onRenameKeydown(e: KeyboardEvent): void {
  if (e.key === 'Enter') { e.preventDefault(); saveRename() }
  if (e.key === 'Escape') { renamingFileUuid.value = null }
}

// ── File reorder + folder drop (#31) ─────────────────────────────────────────

const draggingFileUuid = ref<string | null>(null)
const dropTargetUuid = ref<string | null>(null)   // target file uuid for reorder
const dropTargetFolderId = ref<string | null>(null) // target folder id for move-into
const dropAbove = ref(false)

function onFileDragStart(file: UploadedFile, e: DragEvent): void {
  draggingFileUuid.value = file.file_uuid
  e.dataTransfer!.effectAllowed = 'move'
  e.dataTransfer!.setData('drive-file-uuid', file.file_uuid)
}

function onFileDragOver(file: UploadedFile, e: DragEvent): void {
  if (!draggingFileUuid.value) return
  e.preventDefault()
  e.stopPropagation()
  e.dataTransfer!.dropEffect = 'move'
  dropTargetFolderId.value = null
  dropTargetCrumbIdx.value = null
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
  dropAbove.value = e.clientY - rect.top < rect.height / 2
  dropTargetUuid.value = file.file_uuid
}

function onFileDragLeave(): void {
  dropTargetUuid.value = null
  dropTargetFolderId.value = null
}

async function onFileDrop(targetFile: UploadedFile, e: DragEvent): Promise<void> {
  e.preventDefault()
  e.stopPropagation()
  const srcUuid = draggingFileUuid.value
  draggingFileUuid.value = null
  dropTargetUuid.value = null
  dropTargetFolderId.value = null
  if (!srcUuid || srcUuid === targetFile.file_uuid) return
  const list = [...currentFolder.value.files]
  const srcIdx = list.findIndex((f) => f.file_uuid === srcUuid)
  const tgtIdx = list.findIndex((f) => f.file_uuid === targetFile.file_uuid)
  if (srcIdx === -1 || tgtIdx === -1) return
  const [moved] = list.splice(srcIdx, 1)
  const insertAt = dropAbove.value
    ? tgtIdx > srcIdx ? tgtIdx - 1 : tgtIdx
    : tgtIdx < srcIdx ? tgtIdx + 1 : tgtIdx
  list.splice(insertAt, 0, moved)
  await persistUpdate((node) => ({ ...node, files: list }))
}

// Folder as drop target: move the dragged file into that folder
function onFolderDragOver(folder: DriveFolder, e: DragEvent): void {
  if (!draggingFileUuid.value) return
  e.preventDefault()
  e.stopPropagation()
  e.dataTransfer!.dropEffect = 'move'
  dropTargetUuid.value = null
  dropTargetCrumbIdx.value = null
  dropTargetFolderId.value = folder.id
}

function onFolderDragLeave(e: DragEvent): void {
  // Only clear if we actually left the row element (not a child)
  const related = e.relatedTarget as Node | null
  if (related && (e.currentTarget as HTMLElement).contains(related)) return
  dropTargetFolderId.value = null
}

async function onFolderDrop(folder: DriveFolder, e: DragEvent): Promise<void> {
  e.preventDefault()
  e.stopPropagation()
  const srcUuid = draggingFileUuid.value
  draggingFileUuid.value = null
  dropTargetUuid.value = null
  dropTargetFolderId.value = null
  if (!srcUuid) return

  // Find and detach the file from the current node's file list
  const file = currentFolder.value.files.find((f) => f.file_uuid === srcUuid)
  if (!file) return

  // Build a new root: remove from current location AND append to folder
  const newRoot = updateAtPath(rootFolder.value, currentPath.value, (node) => ({
    ...node,
    files: node.files.filter((f) => f.file_uuid !== srcUuid),
    folders: node.folders.map((fd) =>
      fd.id === folder.id ? { ...fd, files: [...fd.files, file] } : fd,
    ),
  }))

  await blockStore.updateBlock(props.block.id, {
    content: {
      ...props.block.content,
      files: newRoot.files,
      folders: newRoot.folders,
    },
  })
}

function onFileDragEnd(): void {
  draggingFileUuid.value = null
  dropTargetUuid.value = null
  dropTargetFolderId.value = null
  dropTargetCrumbIdx.value = null
}

// ── Breadcrumb drop targets (#37) ─────────────────────────────────────────────

/**
 * Index into the `breadcrumb` array that the user is currently dragging over.
 * 0 = root, 1 = first subfolder crumb, etc.
 * The current (last) crumb is never a drop target since the file is already there.
 */
const dropTargetCrumbIdx = ref<number | null>(null)

function onCrumbDragOver(crumbIdx: number, e: DragEvent): void {
  if (!draggingFileUuid.value) return
  e.preventDefault()
  e.stopPropagation()
  e.dataTransfer!.dropEffect = 'move'
  dropTargetUuid.value = null
  dropTargetFolderId.value = null
  dropTargetCrumbIdx.value = crumbIdx
}

function onCrumbDragLeave(e: DragEvent): void {
  const related = e.relatedTarget as Node | null
  if (related && (e.currentTarget as HTMLElement).contains(related)) return
  dropTargetCrumbIdx.value = null
}

async function onCrumbDrop(crumbIdx: number, e: DragEvent): Promise<void> {
  e.preventDefault()
  e.stopPropagation()
  const srcUuid = draggingFileUuid.value
  draggingFileUuid.value = null
  dropTargetCrumbIdx.value = null
  dropTargetUuid.value = null
  dropTargetFolderId.value = null
  if (!srcUuid) return

  const file = currentFolder.value.files.find((f) => f.file_uuid === srcUuid)
  if (!file) return

  // crumbIdx 0 → root (path []), crumbIdx n → currentPath.slice(0, n)
  const targetPath = currentPath.value.slice(0, crumbIdx)

  // Atomically remove from current location and append to target ancestor.
  let newRoot = updateAtPath(rootFolder.value, currentPath.value, (node) => ({
    ...node,
    files: node.files.filter((f) => f.file_uuid !== srcUuid),
  }))
  newRoot = updateAtPath(newRoot, targetPath, (node) => ({
    ...node,
    files: [...node.files, file],
  }))

  await blockStore.updateBlock(props.block.id, {
    content: {
      ...props.block.content,
      files: newRoot.files,
      folders: newRoot.folders,
    },
  })
}

// ── Search (#38) ──────────────────────────────────────────────────────────────

const searchActive = ref(false)
const searchQuery = ref('')
const searchInputRef = ref<HTMLInputElement | null>(null)

async function activateSearch(): Promise<void> {
  searchActive.value = true
  searchQuery.value = ''
  await nextTick()
  searchInputRef.value?.focus()
}

function deactivateSearch(): void {
  searchActive.value = false
  searchQuery.value = ''
}

function onSearchKeydown(e: KeyboardEvent): void {
  if (e.key === 'Escape') deactivateSearch()
}

/** Recursively collect all files across the whole tree with their folder path. */
function collectAllFiles(node: DriveFolder, path: string[]): SearchResult[] {
  const results: SearchResult[] = node.files.map((f) => ({ file: f, folderPath: path }))
  for (const folder of node.folders) {
    results.push(...collectAllFiles(folder, [...path, folder.name]))
  }
  return results
}

const searchResults = computed<SearchResult[]>(() => {
  const q = searchQuery.value.trim().toLowerCase()
  if (!searchActive.value || !q) return []
  return collectAllFiles(rootFolder.value, []).filter((r) =>
    r.file.filename.toLowerCase().includes(q),
  )
})

const isSearching = computed(() => searchActive.value && searchQuery.value.trim().length > 0)

// ── Collabora ─────────────────────────────────────────────────────────────────

const collaboraUrl = ref<string | null>(null)
const collaboraFilename = ref('')
const collaboraError = ref<string | null>(null)

const EDITABLE_MIMES = new Set([
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  'application/vnd.oasis.opendocument.text',
  'application/vnd.oasis.opendocument.spreadsheet',
  'application/vnd.oasis.opendocument.presentation',
])

function isEditable(mime: string): boolean {
  return EDITABLE_MIMES.has(mime)
}

async function openInCollabora(file: UploadedFile): Promise<void> {
  collaboraError.value = null
  try {
    const res = await fetch('/api/wopi/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({
        file_uuid: file.file_uuid,
        block_id: props.block.id,
        filename: file.filename,
        mime: file.mime,
      }),
    })
    if (!res.ok) { collaboraError.value = 'Editor konnte nicht gestartet werden.'; return }
    const data = await res.json()
    collaboraFilename.value = file.filename
    collaboraUrl.value = data.editor_url
  } catch {
    collaboraError.value = 'Editor konnte nicht gestartet werden.'
  }
}

function closeCollabora(): void {
  collaboraUrl.value = null
  collaboraFilename.value = ''
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function iconForMime(mime: string): string {
  if (mime.startsWith('image/')) return 'mdi:image-outline'
  if (mime.startsWith('video/')) return 'mdi:video-outline'
  if (mime.startsWith('audio/')) return 'mdi:music-note-outline'
  if (mime === 'application/pdf') return 'mdi:file-pdf-box'
  if (mime.startsWith('text/')) return 'mdi:file-document-outline'
  if (mime.includes('zip') || mime.includes('tar') || mime.includes('gzip')) return 'mdi:zip-box-outline'
  if (mime.includes('wordprocessingml') || mime === 'application/vnd.oasis.opendocument.text') return 'mdi:file-word-outline'
  if (mime.includes('spreadsheetml') || mime === 'application/vnd.oasis.opendocument.spreadsheet') return 'mdi:file-excel-outline'
  if (mime.includes('presentationml') || mime === 'application/vnd.oasis.opendocument.presentation') return 'mdi:file-powerpoint-outline'
  return 'mdi:file-outline'
}

// ── Upload handlers ───────────────────────────────────────────────────────────

async function handleDrop(e: DragEvent): Promise<void> {
  if (draggingFileUuid.value) return
  isDropOver.value = false
  const newFiles = await upload.onDropMultiple(e)
  if (newFiles.length) {
    await persistUpdate((node) => ({ ...node, files: [...node.files, ...newFiles] }))
  }
}

async function handleSelect(e: Event): Promise<void> {
  const newFiles = await upload.onFileSelectMultiple(e)
  if (newFiles.length) {
    await persistUpdate((node) => ({ ...node, files: [...node.files, ...newFiles] }))
  }
}

async function handleRemove(fileUuid: string): Promise<void> {
  await upload.deleteFile(fileUuid)
  await persistUpdate((node) => ({
    ...node,
    files: node.files.filter((f) => f.file_uuid !== fileUuid),
  }))
}

// ── Move file to another Drive block ─────────────────────────────────────────

const movingFile = ref<UploadedFile | null>(null)

/**
 * All Drive blocks in the store, excluding the current one.
 * Only blocks that have been loaded (visited pages) are available.
 * The block's display title comes from content.title with 'Drive' fallback.
 */
const otherDriveBlocks = computed(() =>
  Object.values(blockStore.blocks).filter(
    (b) => b.type === 'drive' && b.id !== props.block.id,
  ),
)

function driveBlockTitle(b: typeof otherDriveBlocks.value[number]): string {
  return (b.content?.title as string | undefined) || 'Drive'
}

function openMoveModal(file: UploadedFile): void {
  movingFile.value = file
}

function closeMoveModal(): void {
  movingFile.value = null
}

async function executeMoveFile(targetBlockId: string): Promise<void> {
  const file = movingFile.value
  closeMoveModal()
  if (!file) return

  // 1. Physically move the file on the server first.
  //    Only proceed with index changes if the backend confirms success.
  let newUrl: string
  try {
    const res = await fetch('/api/media/drive-file/move', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({
        file_uuid: file.file_uuid,
        source_block_id: props.block.id,
        target_block_id: targetBlockId,
      }),
    })
    if (!res.ok) {
      console.error('Drive file move failed: HTTP', res.status)
      return
    }
    const data = await res.json()
    newUrl = data.url
  } catch (err) {
    console.error('Drive file move failed:', err)
    return
  }

  // 2. Build the updated file entry with the new URL.
  const movedFile: UploadedFile = { ...file, url: newUrl }

  // 3. Remove from current folder in the source block.
  await persistUpdate((node) => ({
    ...node,
    files: node.files.filter((f) => f.file_uuid !== file.file_uuid),
  }))

  // 4. Append to the root file list of the target block.
  const target = blockStore.blocks[targetBlockId]
  if (!target) return
  const targetFiles = (target.content?.files as UploadedFile[] | undefined) ?? []
  const targetFolders = target.content?.folders ?? []
  await blockStore.updateBlock(targetBlockId, {
    content: {
      ...target.content,
      files: [...targetFiles, movedFile],
      folders: targetFolders,
    },
  })
}

// Total file count across all levels (shown in header)
function countFiles(node: DriveFolder): number {
  return node.files.length + node.folders.reduce((s, f) => s + countFiles(f), 0)
}
const totalFileCount = computed(() => countFiles(rootFolder.value))
</script>

<template>
  <div
    class="drive-block"
    @dragover.prevent.stop="isDropOver = true"
    @dragleave.stop="isDropOver = false"
    @drop.prevent.stop="handleDrop"
  >
    <!-- ── Header ──────────────────────────────────────────────────────────── -->
    <div class="drive-block__header">

      <!-- Back arrow (only when inside a folder) -->
      <button
        v-if="currentPath.length > 0"
        class="drive-block__back-btn"
        title="Zurück"
        @click="navigateBack"
      >
        <Icon icon="mdi:arrow-left" width="15" height="15" />
      </button>

      <!-- Icon button (#30) – only at root level -->
      <div v-else class="drive-block__icon-anchor">
        <button
          class="drive-block__icon-btn"
          title="Symbol ändern"
          @click.stop="showIconPicker = !showIconPicker"
        >
          <Icon :icon="displayIcon" width="15" height="15" />
        </button>
        <IconPicker
          v-if="showIconPicker"
          :model-value="block.icon"
          @update:model-value="onIconUpdate"
          @close="showIconPicker = false"
        />
      </div>

      <!-- Search input (replaces breadcrumb when search is active) (#38) -->
      <template v-if="searchActive">
        <input
          ref="searchInputRef"
          v-model="searchQuery"
          class="drive-block__search-input"
          placeholder="Suchen…"
          @keydown="onSearchKeydown"
          @click.stop
        />
        <button
          class="drive-block__add-btn"
          title="Suche schliessen"
          @click="deactivateSearch"
        >
          <Icon icon="mdi:close" width="14" height="14" />
        </button>
      </template>

      <!-- Breadcrumb / title (hidden while search is active) -->
      <template v-else>
        <span class="drive-block__breadcrumb">
          <!-- Root segment (always editable at root) -->
          <template v-if="currentPath.length === 0">
            <input
              v-if="editingTitle"
              ref="titleInputRef"
              v-model="titleInput"
              class="drive-block__title-input"
              @blur="saveTitle"
              @keydown="onTitleKeydown"
              @click.stop
            />
            <span
              v-else
              class="drive-block__title"
              title="Doppelklick zum Umbenennen"
              @dblclick.stop="startTitleEdit"
            >
              {{ displayTitle }}
              <span class="drive-block__count">{{ totalFileCount }}</span>
            </span>
          </template>

          <!-- Nested path segments (#37: crumb buttons are drop targets) -->
          <template v-else>
            <button
              class="drive-block__crumb-btn"
              :class="{ 'drive-block__crumb-btn--drop-over': dropTargetCrumbIdx === 0 }"
              @click="navigateTo(0)"
              @dragover="onCrumbDragOver(0, $event)"
              @dragleave="onCrumbDragLeave($event)"
              @drop="onCrumbDrop(0, $event)"
            >{{ displayTitle }}</button>
            <template v-for="(crumb, idx) in breadcrumb.slice(1)" :key="crumb.id">
              <Icon icon="mdi:chevron-right" width="12" height="12" class="drive-block__crumb-sep" />
              <!-- Last crumb is current location – not a drop target, shown as plain text -->
              <span
                v-if="idx === breadcrumb.length - 2"
                class="drive-block__crumb-current"
              >{{ crumb.name }}</span>
              <!-- Intermediate crumbs are drop targets (#37) -->
              <button
                v-else
                class="drive-block__crumb-btn"
                :class="{ 'drive-block__crumb-btn--drop-over': dropTargetCrumbIdx === idx + 1 }"
                @click="navigateTo(idx + 1)"
                @dragover="onCrumbDragOver(idx + 1, $event)"
                @dragleave="onCrumbDragLeave($event)"
                @drop="onCrumbDrop(idx + 1, $event)"
              >{{ crumb.name }}</button>
            </template>
          </template>
        </span>

        <!-- Search toggle button (#38) -->
        <button
          class="drive-block__add-btn"
          title="Suchen"
          @click="activateSearch"
        >
          <Icon icon="mdi:magnify" width="14" height="14" />
        </button>

        <!-- New folder button -->
        <button
          class="drive-block__add-btn"
          title="Neuer Ordner"
          @click="startCreateFolder"
        >
          <Icon icon="mdi:folder-plus-outline" width="14" height="14" />
        </button>

        <!-- Add file button -->
        <button
          class="drive-block__add-btn"
          title="Datei hinzufügen"
          :disabled="upload.isUploading.value"
          @click="inputRef?.click()"
        >
          <Icon icon="mdi:plus" width="14" height="14" />
          Add file
        </button>
      </template>

      <input
        ref="inputRef"
        type="file"
        multiple
        class="drive-block__file-input"
        @change="handleSelect"
      />
    </div>

    <!-- Drop overlay -->
    <div v-if="isDropOver && !draggingFileUuid" class="drive-block__drop-overlay">
      <Icon icon="mdi:upload" width="24" height="24" />
      <span>Drop files to add</span>
    </div>

    <!-- ── Search results (#38) ────────────────────────────────────────────── -->
    <template v-if="isSearching">
      <div v-if="searchResults.length > 0" class="drive-block__list">
        <div class="drive-block__list-head">
          <span class="drive-col drive-col--drag" />
          <span class="drive-col drive-col--name">Name</span>
          <span class="drive-col drive-col--size">Size</span>
          <span class="drive-col drive-col--actions" />
        </div>
        <div
          v-for="result in searchResults"
          :key="result.file.file_uuid"
          class="drive-block__row"
        >
          <span class="drive-col drive-col--drag" />
          <span class="drive-col drive-col--name">
            <Icon :icon="iconForMime(result.file.mime)" width="16" height="16" class="drive-block__row-icon" />
            <span class="drive-block__search-name-wrap">
              <span class="drive-block__row-name">{{ result.file.filename }}</span>
              <span v-if="result.folderPath.length > 0" class="drive-block__search-path">
                {{ result.folderPath.join(' / ') }}
              </span>
            </span>
          </span>
          <span class="drive-col drive-col--size drive-block__row-size">
            {{ formatSize(result.file.size) }}
          </span>
          <span class="drive-col drive-col--actions">
            <button
              v-if="isEditable(result.file.mime)"
              class="drive-block__row-action"
              title="In Collabora bearbeiten"
              @click="openInCollabora(result.file)"
            >
              <Icon icon="mdi:pencil-outline" width="14" height="14" />
            </button>
            <button
              class="drive-block__row-action"
              title="In anderen Drive verschieben"
              @click.stop="openMoveModal(result.file)"
            >
              <Icon icon="mdi:folder-move-outline" width="14" height="14" />
            </button>
            <button
              class="drive-block__row-action"
              title="Download"
              @click="downloadFile(result.file.url, result.file.filename)"
            >
              <Icon icon="mdi:download" width="14" height="14" />
            </button>
            <button
              class="drive-block__row-action"
              title="Entfernen"
              @click="handleRemove(result.file.file_uuid)"
            >
              <Icon icon="mdi:close" width="14" height="14" />
            </button>
          </span>
        </div>
      </div>
      <div v-else class="drive-block__empty">
        <span>Keine Dateien gefunden für <em>{{ searchQuery }}</em></span>
      </div>
    </template>

    <!-- ── File / folder list (normal view) ────────────────────────────────── -->
    <template v-else-if="currentFolder.folders.length > 0 || currentFolder.files.length > 0 || creatingFolder">
      <div class="drive-block__list">

        <!-- Column headings -->
        <div class="drive-block__list-head">
          <span class="drive-col drive-col--drag" />
          <span class="drive-col drive-col--name">Name</span>
          <span class="drive-col drive-col--size">Size</span>
          <span class="drive-col drive-col--actions" />
        </div>

        <!-- New folder input row -->
        <div v-if="creatingFolder" class="drive-block__row drive-block__row--new-folder">
          <span class="drive-col drive-col--drag" />
          <span class="drive-col drive-col--name">
            <Icon icon="mdi:folder-outline" width="16" height="16" class="drive-block__row-icon drive-block__row-icon--folder" />
            <input
              ref="newFolderInputRef"
              v-model="newFolderName"
              class="drive-block__rename-input"
              placeholder="Ordnername…"
              @blur="confirmCreateFolder"
              @keydown="onNewFolderKeydown"
              @click.stop
            />
          </span>
          <span class="drive-col drive-col--size" />
          <span class="drive-col drive-col--actions" />
        </div>

        <!-- Folder rows (before files) – also serve as drop targets for file reorder -->
        <div
          v-for="folder in currentFolder.folders"
          :key="folder.id"
          class="drive-block__row drive-block__row--folder"
          :class="{ 'drive-block__row--drop-into': dropTargetFolderId === folder.id }"
          @click="navigateInto(folder)"
          @dragover="onFolderDragOver(folder, $event)"
          @dragleave="onFolderDragLeave($event)"
          @drop="onFolderDrop(folder, $event)"
        >
          <span class="drive-col drive-col--drag" />
          <span class="drive-col drive-col--name">
            <Icon icon="mdi:folder-outline" width="16" height="16" class="drive-block__row-icon drive-block__row-icon--folder" />
            <input
              v-if="renamingFolderId === folder.id"
              ref="folderRenameInputRef"
              v-model="folderRenameValue"
              class="drive-block__rename-input"
              @blur="saveFolderRename"
              @keydown="onFolderRenameKeydown"
              @click.stop
            />
            <span
              v-else
              class="drive-block__row-name"
              @dblclick.stop="startFolderRename(folder)"
            >
              {{ folder.name }}
              <span class="drive-block__folder-count">{{ countFiles(folder) }}</span>
            </span>
          </span>
          <span class="drive-col drive-col--size drive-block__row-size">—</span>
          <span class="drive-col drive-col--actions" @click.stop>
            <button
              class="drive-block__row-action"
              title="Umbenennen"
              @click.stop="startFolderRename(folder)"
            >
              <Icon icon="mdi:pencil-outline" width="14" height="14" />
            </button>
            <button
              class="drive-block__row-action"
              title="Ordner löschen"
              @click.stop="removeFolder(folder.id)"
            >
              <Icon icon="mdi:close" width="14" height="14" />
            </button>
          </span>
        </div>

        <!-- File rows -->
        <div
          v-for="file in currentFolder.files"
          :key="file.file_uuid"
          class="drive-block__row"
          :class="{
            'drive-block__row--drop-above': dropTargetUuid === file.file_uuid && dropAbove,
            'drive-block__row--drop-below': dropTargetUuid === file.file_uuid && !dropAbove,
            'drive-block__row--dragging': draggingFileUuid === file.file_uuid,
          }"
          @dragover="onFileDragOver(file, $event)"
          @dragleave="onFileDragLeave"
          @drop="onFileDrop(file, $event)"
        >
          <!-- Drag handle -->
          <span
            class="drive-col drive-col--drag"
            draggable="true"
            title="Verschieben"
            @dragstart="onFileDragStart(file, $event)"
            @dragend="onFileDragEnd"
          >
            <Icon icon="mdi:drag-vertical" width="14" height="14" class="drive-block__drag-handle" />
          </span>

          <!-- Name -->
          <span class="drive-col drive-col--name">
            <Icon :icon="iconForMime(file.mime)" width="16" height="16" class="drive-block__row-icon" />
            <input
              v-if="renamingFileUuid === file.file_uuid"
              ref="renameInputRef"
              v-model="renameValue"
              class="drive-block__rename-input"
              @blur="saveRename"
              @keydown="onRenameKeydown"
              @click.stop
            />
            <span
              v-else
              class="drive-block__row-name"
              title="Doppelklick zum Umbenennen"
              @dblclick.stop="startRename(file)"
            >
              {{ file.filename }}
            </span>
          </span>

          <span class="drive-col drive-col--size drive-block__row-size">
            {{ formatSize(file.size) }}
          </span>

          <span class="drive-col drive-col--actions">
            <button
              v-if="isEditable(file.mime)"
              class="drive-block__row-action"
              title="In Collabora bearbeiten"
              @click="openInCollabora(file)"
            >
              <Icon icon="mdi:pencil-outline" width="14" height="14" />
            </button>
            <button
              class="drive-block__row-action"
              title="In anderen Drive verschieben"
              @click.stop="openMoveModal(file)"
            >
              <Icon icon="mdi:folder-move-outline" width="14" height="14" />
            </button>
            <button
              class="drive-block__row-action"
              title="Download"
              @click="downloadFile(file.url, file.filename)"
            >
              <Icon icon="mdi:download" width="14" height="14" />
            </button>
            <button
              class="drive-block__row-action"
              title="Entfernen"
              @click="handleRemove(file.file_uuid)"
            >
              <Icon icon="mdi:close" width="14" height="14" />
            </button>
          </span>
        </div>
      </div>
    </template>

    <!-- Empty state -->
    <div v-else class="drive-block__empty">
      <span>Drop files here or click <em>Add file</em></span>
    </div>

    <!-- Upload progress -->
    <div v-if="upload.isUploading.value" class="drive-block__uploading">
      <Icon icon="mdi:loading" width="14" height="14" class="drive-block__spinner" />
      Uploading…
    </div>

    <div v-if="upload.error.value" class="drive-block__error">{{ upload.error.value }}</div>
    <div v-if="collaboraError" class="drive-block__error">{{ collaboraError }}</div>
  </div>

  <!-- Collabora modal -->
  <Teleport to="body">
    <div v-if="collaboraUrl" class="drive-editor-overlay" @click.self="closeCollabora">
      <div class="drive-editor-container">
        <div class="drive-editor-header">
          <Icon icon="mdi:file-edit-outline" width="16" height="16" class="drive-editor-header-icon" />
          <span class="drive-editor-title">{{ collaboraFilename }}</span>
          <button class="drive-editor-close" title="Schliessen" @click="closeCollabora">
            <Icon icon="mdi:close" width="18" height="18" />
          </button>
        </div>
        <iframe
          :src="collaboraUrl"
          class="drive-editor-iframe"
          allow="fullscreen"
          referrerpolicy="origin"
        />
      </div>
    </div>
  </Teleport>

  <!-- Move-file modal -->
  <Teleport to="body">
    <div v-if="movingFile" class="drive-move-overlay" @click.self="closeMoveModal">
      <div class="drive-move-modal">
        <div class="drive-move-modal__header">
          <Icon icon="mdi:folder-move-outline" width="16" height="16" class="drive-move-modal__icon" />
          <span class="drive-move-modal__title">
            "{{ movingFile.filename }}" verschieben nach…
          </span>
          <button class="drive-move-modal__close" title="Abbrechen" @click="closeMoveModal">
            <Icon icon="mdi:close" width="16" height="16" />
          </button>
        </div>
        <div class="drive-move-modal__list">
          <div
            v-for="target in otherDriveBlocks"
            :key="target.id"
            class="drive-move-modal__item"
            @click="executeMoveFile(target.id)"
          >
            <Icon :icon="target.icon ?? 'mdi:folder-outline'" width="16" height="16" class="drive-move-modal__item-icon" />
            <span class="drive-move-modal__item-name">{{ driveBlockTitle(target) }}</span>
            <Icon icon="mdi:chevron-right" width="14" height="14" class="drive-move-modal__item-arrow" />
          </div>
          <div v-if="otherDriveBlocks.length === 0" class="drive-move-modal__empty">
            Keine anderen Drive-Blöcke gefunden.
            Navigiere zu einer Seite mit einem Drive-Block, damit er hier erscheint.
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.drive-block {
  flex: 1;
  min-width: 0;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  overflow: hidden;
  position: relative;
  background: var(--color-surface);
}

/* ── Header ──────────────────────────────────────────────────────────────── */
.drive-block__header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 12px;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-hover);
  min-height: 38px;
}

/* Back arrow */
.drive-block__back-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--color-text-muted);
  cursor: pointer;
  flex-shrink: 0;
  transition: background 0.1s, color 0.1s;
}

.drive-block__back-btn:hover {
  background: var(--color-active);
  color: var(--color-text);
}

/* Icon picker anchor (#30) */
.drive-block__icon-anchor {
  position: relative;
  flex-shrink: 0;
}

.drive-block__icon-btn {
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
  transition: background 0.1s, color 0.1s;
}

.drive-block__icon-btn:hover {
  background: var(--color-active);
  color: var(--color-text);
}

.drive-block__icon-anchor :deep(.icon-picker) {
  top: calc(100% + 4px);
  left: 0;
}

/* Breadcrumb */
.drive-block__breadcrumb {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 3px;
  min-width: 0;
  overflow: hidden;
}

.drive-block__title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--color-text);
  cursor: default;
  user-select: none;
  white-space: nowrap;
}

.drive-block__title-input {
  font-size: 0.8125rem;
  font-weight: 600;
  font-family: inherit;
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-accent);
  border-radius: 4px;
  padding: 1px 5px;
  outline: none;
  width: 120px;
}

.drive-block__count,
.drive-block__folder-count {
  font-weight: 400;
  color: var(--color-text-muted);
  font-size: 0.75rem;
}

.drive-block__crumb-btn {
  background: none;
  border: none;
  padding: 2px 4px;
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--color-text-muted);
  cursor: pointer;
  white-space: nowrap;
  border-radius: 3px;
  transition: color 0.1s, background 0.1s;
}

.drive-block__crumb-btn:hover {
  color: var(--color-text);
}

/* Drop-over highlight for breadcrumb ancestors (#37) */
.drive-block__crumb-btn--drop-over {
  background: var(--color-accent-subtle, color-mix(in srgb, var(--color-accent) 12%, transparent));
  color: var(--color-accent);
  outline: 1px solid var(--color-accent);
  outline-offset: -1px;
}

.drive-block__crumb-sep {
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.drive-block__crumb-current {
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--color-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Add buttons */
.drive-block__add-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  border: 1px solid var(--color-border);
  border-radius: 5px;
  background: var(--color-surface);
  color: var(--color-text);
  font-size: 0.75rem;
  cursor: pointer;
  transition: background 0.1s;
  flex-shrink: 0;
}

.drive-block__add-btn:hover:not(:disabled) {
  background: var(--color-active);
}

.drive-block__add-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.drive-block__file-input {
  display: none;
}

/* Search input (#38) */
.drive-block__search-input {
  flex: 1;
  min-width: 0;
  font-size: 0.8125rem;
  font-family: inherit;
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-accent);
  border-radius: 5px;
  padding: 3px 8px;
  outline: none;
  height: 26px;
}

/* ── Drop overlay ────────────────────────────────────────────────────────── */
.drive-block__drop-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  background: var(--color-accent-subtle, color-mix(in srgb, var(--color-accent) 10%, transparent));
  border: 2px dashed var(--color-accent);
  border-radius: 8px;
  color: var(--color-accent);
  font-size: 0.875rem;
  font-weight: 500;
  z-index: 2;
  pointer-events: none;
}

/* ── List ────────────────────────────────────────────────────────────────── */
.drive-block__list {
  font-size: 0.8125rem;
}

.drive-block__list-head {
  display: flex;
  align-items: center;
  padding: 4px 12px;
  font-size: 0.7rem;
  font-weight: 600;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--color-text-muted);
  border-bottom: 1px solid var(--color-border);
}

.drive-block__row {
  display: flex;
  align-items: center;
  padding: 5px 12px;
  transition: background 0.1s;
  position: relative;
}

.drive-block__row:not(:last-child) {
  border-bottom: 1px solid var(--color-border);
}

.drive-block__row:hover {
  background: var(--color-hover);
}

/* Folder rows get a pointer and slightly different hover */
.drive-block__row--folder {
  cursor: pointer;
}

/* Highlight when a file is dragged over a folder */
.drive-block__row--drop-into {
  background: var(--color-accent-subtle, color-mix(in srgb, var(--color-accent) 10%, transparent));
  outline: 1px solid var(--color-accent);
  outline-offset: -1px;
}

.drive-block__row--new-folder {
  background: var(--color-hover);
}

/* Drop indicators for file reorder */
.drive-block__row--drop-above::before,
.drive-block__row--drop-below::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  height: 2px;
  background: var(--color-accent);
  border-radius: 1px;
  pointer-events: none;
}

.drive-block__row--drop-above::before { top: 0; }
.drive-block__row--drop-below::after { bottom: 0; }
.drive-block__row--dragging { opacity: 0.4; }

/* Columns */
.drive-col--drag {
  width: 18px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}

.drive-col--name {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 7px;
}

.drive-col--size {
  width: 72px;
  text-align: right;
  flex-shrink: 0;
}

/* Width: collabora + move + download + remove = 4 x 22px + 3 x 2px gap = 94px */
.drive-col--actions {
  display: flex;
  align-items: center;
  gap: 2px;
  width: 94px;
  justify-content: flex-end;
  flex-shrink: 0;
  opacity: 0;
  transition: opacity 0.1s;
}

.drive-block__row:hover .drive-col--actions {
  opacity: 1;
}

/* Drag handle */
.drive-block__drag-handle {
  color: var(--color-text-muted);
  opacity: 0;
  cursor: grab;
  transition: opacity 0.1s;
}

.drive-block__row:hover .drive-block__drag-handle {
  opacity: 1;
}

/* Icons */
.drive-block__row-icon {
  flex-shrink: 0;
  color: var(--color-text-muted);
}

.drive-block__row-icon--folder {
  color: var(--color-accent);
}

.drive-block__row-name {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--color-text);
  cursor: default;
  display: flex;
  align-items: center;
  gap: 5px;
}

.drive-block__rename-input {
  flex: 1;
  min-width: 0;
  font-size: 0.8125rem;
  font-family: inherit;
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-accent);
  border-radius: 3px;
  padding: 0 4px;
  outline: none;
  height: 20px;
}

.drive-block__row-size {
  color: var(--color-text-muted);
  font-size: 0.75rem;
}

.drive-block__row-action {
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

.drive-block__row-action:hover {
  background: var(--color-active);
  color: var(--color-text);
}

/* Search result path indicator (#38) */
.drive-block__search-name-wrap {
  display: flex;
  flex-direction: column;
  min-width: 0;
  gap: 1px;
}

.drive-block__search-path {
  font-size: 0.7rem;
  color: var(--color-text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ── Empty / status ──────────────────────────────────────────────────────── */
.drive-block__empty {
  padding: 16px 12px;
  font-size: 0.8125rem;
  color: var(--color-text-muted);
  text-align: center;
}

.drive-block__uploading {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  font-size: 0.75rem;
  color: var(--color-text-muted);
  border-top: 1px solid var(--color-border);
}

.drive-block__spinner {
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.drive-block__error {
  padding: 6px 12px;
  font-size: 0.75rem;
  color: #e05353;
  border-top: 1px solid var(--color-border);
}

/* ── Collabora modal ─────────────────────────────────────────────────────── */
.drive-editor-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: rgba(0, 0, 0, 0.55);
  display: flex;
  align-items: stretch;
  justify-content: stretch;
  padding: 24px;
  box-sizing: border-box;
}

.drive-editor-container {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: var(--color-surface);
  border-radius: 8px;
  overflow: hidden;
  box-shadow: 0 8px 40px rgba(0, 0, 0, 0.35);
}

.drive-editor-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-hover);
  flex-shrink: 0;
}

.drive-editor-header-icon {
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.drive-editor-title {
  flex: 1;
  font-size: 0.875rem;
  font-weight: 500;
  color: var(--color-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.drive-editor-close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: none;
  border-radius: 5px;
  background: transparent;
  color: var(--color-text-muted);
  cursor: pointer;
  flex-shrink: 0;
  transition: background 0.1s, color 0.1s;
}

.drive-editor-close:hover {
  background: var(--color-active);
  color: var(--color-text);
}

.drive-editor-iframe {
  flex: 1;
  width: 100%;
  border: none;
}

/* ── Move-file modal ─────────────────────────────────────────────────────── */
.drive-move-overlay {
  position: fixed;
  inset: 0;
  z-index: 1001;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
}

.drive-move-modal {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
  width: 340px;
  max-height: 60vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.drive-move-modal__header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-hover);
  flex-shrink: 0;
}

.drive-move-modal__icon {
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.drive-move-modal__title {
  flex: 1;
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--color-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.drive-move-modal__close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--color-text-muted);
  cursor: pointer;
  flex-shrink: 0;
  transition: background 0.1s, color 0.1s;
}

.drive-move-modal__close:hover {
  background: var(--color-active);
  color: var(--color-text);
}

.drive-move-modal__list {
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: var(--color-border) transparent;
}

.drive-move-modal__item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  cursor: pointer;
  transition: background 0.1s;
}

.drive-move-modal__item:not(:last-child) {
  border-bottom: 1px solid var(--color-border);
}

.drive-move-modal__item:hover {
  background: var(--color-hover);
}

.drive-move-modal__item-icon {
  flex-shrink: 0;
  color: var(--color-accent);
}

.drive-move-modal__item-name {
  flex: 1;
  font-size: 0.875rem;
  color: var(--color-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.drive-move-modal__item-arrow {
  flex-shrink: 0;
  color: var(--color-text-muted);
}

.drive-move-modal__empty {
  padding: 20px 16px;
  font-size: 0.8125rem;
  color: var(--color-text-muted);
  text-align: center;
  line-height: 1.5;
}
</style>

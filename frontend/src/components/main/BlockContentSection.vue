<script setup lang="ts">
/**
 * BlockContentSection
 *
 * Renders the list of direct children of a given parent block as an
 * editable document section.
 *
 * Text-type blocks (paragraph, heading_1/2/3, bulleted_list_item,
 * numbered_list_item, to_do, toggle, quote, callout) are rendered through
 * BlockEditorRow and support inline editing and slash commands.
 *
 * The divider type is rendered as a plain <hr> with no editing interaction.
 *
 * Media/file/meta blocks (image, video, audio, pdf, file, drive, bookmark,
 * embed) are dispatched to dedicated block components via a type →
 * component map. They receive only the block and parentId props; all content
 * mutations go through the block store directly.
 *
 * Layout blocks (Tier 3) are dispatched to LayoutBlock, which manages
 * its column children and resize dividers independently.
 * Synched blocks (synched_origin, synched_mirror) are dispatched to their
 * dedicated components. synched_origin is a plain container; synched_mirror
 * renders the origin's children and supports a per-block Lock toggle in the
 * context menu.
 *
 * Structural blocks (page, database, …) continue to render as navigable
 * list items with the existing icon / label representation.
 * Database blocks can optionally be rendered inline via the context menu.   (#8)
 *
 * Toggle blocks additionally render a nested BlockContentSection for their
 * children when open; the open/closed state is stored locally per session.
 *
 * All blocks retain drag-and-drop reordering, including within, into and out
 * of open toggle blocks. For text blocks the drag is intentionally only
 * triggered via the drag handle to avoid conflicting with text selection
 * inside the textarea. For layout blocks, only the block-level drag handle
 * initiates a block drag; column handles inside LayoutBlock use a separate
 * column-mode drag that this component ignores.
 *
 * Drag handle interactions:
 *   Plain click            → context menu (duplicate / delete / inline / add column)
 *   Ctrl+Click             → insert empty block below
 *   Shift+Click            → insert empty block above
 *   Ctrl+Shift+Click       → duplicate
 *   Ctrl+Alt+Click         → delete
 *
 * Changes vs. original
 * --------------------
 * #5  Context-menu position clamps to viewport via nextTick + measured dimensions.
 * #8  Database blocks can be shown as an inline table via the context menu.
 *     State is persisted in localStorage (key: db-inline-ids).
 * #9  onAddBlock only creates a new block when the last one is not already an
 *     empty paragraph — otherwise it focuses that block instead.
 * #10 draggable="true" is now on the handle span for all block types, not the
 *     outer content-block div. This prevents the browser from intercepting
 *     clicks anywhere on the block as a drag gesture, which previously broke
 *     native textarea cursor placement. onDragStart cancels drag events
 *     (e.preventDefault) that do not originate from the handle.
 * #11 Layout blocks expose an "Add column" item in the drag handle context menu
 *     (visible when column count < 4). The footer button inside LayoutBlock has
 *     been removed in favour of this placement.
 */
import { computed, ref, watch, nextTick, onMounted, type Component } from 'vue'
import { useRouter } from 'vue-router'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { useBlockStore, type Block } from '@/stores/blocks'
import { useDrag } from '@/composables/useDrag'
import { BLOCK_COLORS, BLOCK_BG_COLORS, resolveColor, resolveBgColor } from '@/composables/blockColors'
import BlockEditorRow from '@/components/editor/BlockEditorRow.vue'
// Self-import for recursive toggle children rendering.
import BlockContentSection from '@/components/main/BlockContentSection.vue'
// Tier 2 block components
import ImageBlock from '@/components/editor/blocks/ImageBlock.vue'
import VideoBlock from '@/components/editor/blocks/VideoBlock.vue'
import AudioBlock from '@/components/editor/blocks/AudioBlock.vue'
import PdfBlock from '@/components/editor/blocks/PdfBlock.vue'
import FileBlock from '@/components/editor/blocks/FileBlock.vue'
import DriveBlock from '@/components/editor/blocks/DriveBlock.vue'
import BookmarkBlock from '@/components/editor/blocks/BookmarkBlock.vue'
import EmbedBlock from '@/components/editor/blocks/EmbedBlock.vue'
// Tier 3 block components
import LayoutBlock from '@/components/editor/blocks/layout/LayoutBlock.vue'
import SynchedOriginBlock from '@/components/editor/blocks/layout/SynchedOriginBlock.vue'
import SynchedMirrorBlock from '@/components/editor/blocks/layout/SynchedMirrorBlock.vue'
// #8 – for inline database view
import DatabaseBlock from '@/components/editor/blocks/DatabaseBlock.vue'
// #100 – table of contents block
import TableOfContentsBlock from '@/components/editor/blocks/TableOfContentsBlock.vue'
import { useToggleState } from '@/composables/useToggleState'
import { useDatabaseTemplatesStore } from '@/stores/databaseTemplates'

// ── Props ─────────────────────────────────────────────────────────────────────

const props = defineProps<{
  parentId: string
  /** True when rendered as children of a toggle block — reduces padding. */
  nested?: boolean
  /**
   * When the parent block is a database entry or template, pass the owning
   * database block ID here.  The empty state uses it to display the template
   * picker for that database.
   */
  databaseId?: string
}>()

// ── Dependencies ──────────────────────────────────────────────────────────────

const { t } = useI18n()
const router = useRouter()
const blockStore = useBlockStore()
const drag = useDrag()
const templateStore = useDatabaseTemplatesStore()

// ── Children ──────────────────────────────────────────────────────────────────

const children = computed<Block[]>(() =>
  blockStore.getChildren(props.parentId).filter((b) => b.state === 'active'),
)
const isEmpty = computed(() => children.value.length === 0)

// Initial load — MainView already fetches, but guard against direct use.
onMounted(async () => {
  if (!blockStore.hasLoadedChildren(props.parentId)) {
    await blockStore.fetchChildren(props.parentId)
  }
})

// Re-fetch whenever the cache is invalidated. This fires after any mutation
// (createBlock, moveBlock, deleteBlock) and after WebSocket 'created' events,
// both of which delete childrenMap[parentId]. Without this watcher the
// content area would appear empty until a manual page reload.
watch(
  () => blockStore.childrenMap[props.parentId],
  (val) => {
    if (val === undefined) {
      blockStore.fetchChildren(props.parentId)
    }
  },
)

// ── Block type classification ─────────────────────────────────────────────────

const TEXT_TYPES = new Set([
  'paragraph',
  'text_toggle',
  'heading_1',
  'heading_2',
  'heading_3',
  'heading_4',
  'heading_1_toggle',
  'heading_2_toggle',
  'heading_3_toggle',
  'heading_4_toggle',
  'bulleted_list_item',
  'numbered_list_item',
  'to_do',
  'toggle',
  'quote',
  'callout',
])

const MEDIA_COMPONENT_MAP: Record<string, Component> = {
  image: ImageBlock,
  video: VideoBlock,
  audio: AudioBlock,
  pdf: PdfBlock,
  file: FileBlock,
  drive: DriveBlock,
  bookmark: BookmarkBlock,
  embed: EmbedBlock,
  table_of_contents: TableOfContentsBlock,
}

// ── Heading type helpers ───────────────────────────────────────────────────────

/** All four collapsible heading type strings. */
const TOGGLE_HEADING_TYPES = new Set([
  'heading_1_toggle',
  'heading_2_toggle',
  'heading_3_toggle',
  'heading_4_toggle',
])

function isToggleHeadingType(type: string): boolean {
  return TOGGLE_HEADING_TYPES.has(type)
}

function isHeadingType(type: string): boolean {
  return /^heading_[1-4](_toggle)?$/.test(type)
}

/** All block types that render a toggle chevron and a nested children section. */
const ALL_TOGGLE_TYPES = new Set([
  'toggle',
  'text_toggle',
  'heading_1_toggle',
  'heading_2_toggle',
  'heading_3_toggle',
  'heading_4_toggle',
])

function isTextBlock(block: Block): boolean {
  return TEXT_TYPES.has(block.type)
}

function isMediaBlock(block: Block): boolean {
  return Object.prototype.hasOwnProperty.call(MEDIA_COMPONENT_MAP, block.type)
}

function isLayoutBlock(block: Block): boolean {
  return block.type === 'layout'
}

function isSynchedBlock(block: Block): boolean {
  return block.type === 'synched_origin' || block.type === 'synched_mirror'
}

function mediaComponentFor(type: string): Component | null {
  return MEDIA_COMPONENT_MAP[type] ?? null
}

// ── Focus management ──────────────────────────────────────────────────────────

/** ID of the block that should receive focus on the next render cycle. */
const pendingFocusBlockId = ref<string | null>(null)

function onFocusConsumed(): void {
  pendingFocusBlockId.value = null
}

// ── Toggle open/closed state ──────────────────────────────────────────────────

/** Local per-session open state for toggle blocks, keyed by block ID. */
const { toggleOpenStates } = useToggleState()

async function onToggleOpen(blockId: string): Promise<void> {
  toggleOpenStates.value[blockId] = !(toggleOpenStates.value[blockId] ?? false)
}

/**
 * Returns the effective open/closed state for any block that has a toggle
 * chevron. All toggle types use local per-session state.
 */
function getToggleOpen(block: Block): boolean | undefined {
  if (ALL_TOGGLE_TYPES.has(block.type)) return toggleOpenStates.value[block.id] ?? false
  return undefined
}

/**
 * Returns true for block types that can be toggled between their static and
 * collapsible variants via the context menu or keyboard shortcut.
 */
function isToggleConvertible(type: string): boolean {
  return type === 'paragraph' || type === 'text_toggle' || isHeadingType(type)
}

// ── Numbered list index ───────────────────────────────────────────────────────

/**
 * Returns the 1-based position of a numbered_list_item within its current
 * consecutive run. The count resets whenever the run is interrupted by a
 * block of a different type.
 */
function listIndexFor(block: Block): number {
  if (block.type !== 'numbered_list_item') return 0
  const siblings = children.value
  const blockIdx = siblings.findIndex((b) => b.id === block.id)
  let count = 0
  for (let i = blockIdx; i >= 0; i--) {
    if (siblings[i].type !== 'numbered_list_item') break
    count++
  }
  return count
}

// ── Block creation ────────────────────────────────────────────────────────────

async function onCreateAfter(afterBlockId: string, type: string): Promise<void> {
  const siblings = children.value
  const idx = siblings.findIndex((b) => b.id === afterBlockId)
  const afterPos = siblings[idx]?.position ?? 0
  const nextPos = idx < siblings.length - 1 ? siblings[idx + 1].position : null

  const position =
    nextPos !== null
      ? (afterPos + nextPos) / 2
      : afterPos + 1

  // Only text blocks start with an empty text content; media/meta blocks start
  // with no content so their upload zone is shown immediately.
  // Layout blocks start with a widths array; column bootstrapping is handled
  // by LayoutBlock.onMounted. Synched origin starts with an empty content object.
  const initialContent: Record<string, unknown> | undefined =
    type === 'layout'
      ? { widths: [0.5, 0.5] }
      : type === 'synched_origin'
        ? {}
        : TEXT_TYPES.has(type)
          ? { text: '' }
          : undefined

  const block = await blockStore.createBlock({
    type,
    parent_id: props.parentId,
    position,
    content: initialContent,
  })

  // createBlock invalidates childrenMap, triggering the watcher above to
  // refetch. We eagerly force-refetch here so the new block is in the DOM
  // before we request focus.
  await blockStore.fetchChildren(props.parentId, true)
  await nextTick()

  if (isTextBlock(block)) {
    pendingFocusBlockId.value = block.id
  }
}

// ── Block deletion ────────────────────────────────────────────────────────────

async function onDeleteSelf(blockId: string): Promise<void> {
  const siblings = children.value
  const idx = siblings.findIndex((b) => b.id === blockId)
  const previousBlock = idx > 0 ? siblings[idx - 1] : null

  await blockStore.deleteBlock(blockId, props.parentId)

  await blockStore.fetchChildren(props.parentId, true)
  await nextTick()

  if (previousBlock && isTextBlock(previousBlock)) {
    pendingFocusBlockId.value = previousBlock.id
  }
}

// ── Keyboard navigation between blocks ───────────────────────────────────────

function onNavigate(blockId: string, direction: 'up' | 'down'): void {
  const siblings = children.value
  const idx = siblings.findIndex((b) => b.id === blockId)
  const target = direction === 'up' ? siblings[idx - 1] : siblings[idx + 1]
  if (target && isTextBlock(target)) {
    pendingFocusBlockId.value = target.id
  }
}

// ── #9 – Add block affordance (clicking empty area) ─────────────────────────
// Only create a new block when the last one is not already an empty paragraph;
// otherwise focus that block instead.

async function onAddBlock(): Promise<void> {
  const kids = children.value
  const last = kids[kids.length - 1]

  // Guard: if the last block is already an empty paragraph, just focus it.
  if (last && last.type === 'paragraph' && ((last.content?.text as string | undefined) ?? '') === '') {
    pendingFocusBlockId.value = last.id
    return
  }

  const position = last ? last.position + 1 : 1

  const block = await blockStore.createBlock({
    type: 'paragraph',
    parent_id: props.parentId,
    position,
    content: { text: '' },
  })

  await blockStore.fetchChildren(props.parentId, true)
  await nextTick()
  pendingFocusBlockId.value = block.id
}

// ── Template apply (from empty-state picker) ─────────────────────────────────

async function applyTemplate(templateId: string): Promise<void> {
  if (!props.databaseId) return
  await templateStore.applyTemplate(props.databaseId, templateId, props.parentId)
  // Refresh entry content after apply.
  await blockStore.fetchChildren(props.parentId, true)
}

// ── Non-text block helpers ────────────────────────────────────────────────────

function iconFor(block: Block): string {
  if (block.type === 'linked_database') {
    if (!block.reference_id) return 'mdi:table-arrow-right'
    const ref = blockStore.blocks[block.reference_id]
    return ref?.icon ?? 'mdi:table-arrow-right'
  }
  if (block.icon) return block.icon
  if (block.type === 'database') return 'mdi:table-large'
  return 'mdi:file-outline'
}

function labelFor(block: Block): string {
  if (block.type === 'linked_database') {
    if (!block.reference_id) return t('linkedDb.noReference')
    const ref = blockStore.blocks[block.reference_id]
    return (ref?.content?.title as string | undefined) ?? t('main.untitled')
  }
  return (block.content?.title as string | undefined) ?? t('main.untitled')
}

function onBlockLinkClick(block: Block): void {
  if (block.type === 'page' || block.type === 'database') {
    router.push(`/blocks/${block.id}`)
  }
}

// ── Block duplication ─────────────────────────────────────────────────────────

async function duplicateBlock(block: Block): Promise<void> {
  const newBlock = await blockStore.deepDuplicateBlock(block.id, props.parentId)

  await blockStore.fetchChildren(props.parentId, true)
  await nextTick()

  if (isTextBlock(newBlock)) {
    pendingFocusBlockId.value = newBlock.id
  }
}

// ── Block insertion via handle shortcuts ──────────────────────────────────────

async function insertEmptyBlockAbove(block: Block): Promise<void> {
  const siblings = children.value
  const idx = siblings.findIndex((b) => b.id === block.id)
  const beforePos = idx > 0 ? siblings[idx - 1].position : null
  const position =
    beforePos !== null
      ? (beforePos + block.position) / 2
      : block.position / 2

  const newBlock = await blockStore.createBlock({
    type: 'paragraph',
    parent_id: props.parentId,
    position,
    content: { text: '' },
  })

  await blockStore.fetchChildren(props.parentId, true)
  await nextTick()
  pendingFocusBlockId.value = newBlock.id
}

async function insertEmptyBlockBelow(block: Block): Promise<void> {
  await onCreateAfter(block.id, 'paragraph')
}

/**
 * Create a linked_database block after *afterBlockId*, pointing to *targetDbId*.
 * The referenced database block is fetched into the store so that iconFor/labelFor
 * can resolve its title without a second render cycle.
 */
async function onCreateLinkedDb(afterBlockId: string, targetDbId: string): Promise<void> {
  const siblings = children.value
  const idx = siblings.findIndex((b) => b.id === afterBlockId)
  const afterPos = siblings[idx]?.position ?? 0
  const nextPos  = idx < siblings.length - 1 ? siblings[idx + 1].position : null
  const position = nextPos !== null ? (afterPos + nextPos) / 2 : afterPos + 1

  await blockStore.createBlock({
    type: 'linked_database',
    parent_id: props.parentId,
    position,
    reference_id: targetDbId,
  })

  // Ensure the referenced DB block is in the store so the row renders correctly.
  if (!blockStore.blocks[targetDbId]) {
    blockStore.fetchBlock(targetDbId).catch(() => {})
  }

  await blockStore.fetchChildren(props.parentId, true)
}

// Fetch referenced DB blocks for linked_database children that are not yet
// cached. Runs immediately and whenever the children list changes.
watch(
  children,
  (blocks) => {
    for (const b of blocks) {
      if (b.type === 'linked_database' && b.reference_id && !blockStore.blocks[b.reference_id]) {
        blockStore.fetchBlock(b.reference_id).catch(() => {})
      }
    }
  },
  { immediate: true },
)

// ── #8 – Inline-Datenbankansicht (persistent via localStorage) ───────────────
// Speichert IDs von Datenbank-Blöcken, die aktuell inline angezeigt werden.
// Der Zustand wird in localStorage unter DB_INLINE_STORAGE_KEY abgelegt, damit
// er über Seitenreloads hinweg erhalten bleibt.

const DB_INLINE_STORAGE_KEY = 'db-inline-ids'

function loadDbInlineIds(): Set<string> {
  try {
    const raw = localStorage.getItem(DB_INLINE_STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      if (Array.isArray(parsed)) return new Set<string>(parsed)
    }
  } catch {
    // Ungültiger localStorage-Wert – mit leerem Set starten.
  }
  return new Set<string>()
}

function saveDbInlineIds(ids: Set<string>): void {
  try {
    localStorage.setItem(DB_INLINE_STORAGE_KEY, JSON.stringify([...ids]))
  } catch {
    // localStorage nicht verfügbar (z. B. Private Browsing mit vollem Speicher).
  }
}

const dbInlineIds = ref<Set<string>>(loadDbInlineIds())

function isDbInline(blockId: string): boolean {
  return dbInlineIds.value.has(blockId)
}

function toggleDbInline(blockId: string): void {
  const next = new Set(dbInlineIds.value)
  if (next.has(blockId)) {
    next.delete(blockId)
  } else {
    next.add(blockId)
  }
  dbInlineIds.value = next
  saveDbInlineIds(next)
}

// ── Context menu ──────────────────────────────────────────────────────────────

interface ContextMenuState {
  visible: boolean
  x: number
  y: number
  blockId: string | null
}

const contextMenu = ref<ContextMenuState>({ visible: false, x: 0, y: 0, blockId: null })

/** Ref auf das gerenderte Kontextmenü-Element für Viewport-Clamp (#5). */
const contextMenuEl = ref<HTMLElement | null>(null)

// ── Drive block delete confirmation ───────────────────────────────────────────

/** ID des Drive-Blocks, für den der Lösch-Dialog offen ist. */
const driveDeleteConfirmId = ref<string | null>(null)

function openDriveDeleteConfirm(blockId: string): void {
  driveDeleteConfirmId.value = blockId
}

function closeDriveDeleteConfirm(): void {
  driveDeleteConfirmId.value = null
}

async function confirmDriveDelete(): Promise<void> {
  const id = driveDeleteConfirmId.value
  closeDriveDeleteConfirm()
  if (id) await onDeleteSelf(id)
}

/**
 * Der Block, auf den sich das aktuelle Kontextmenü bezieht.
 * Wird im Template genutzt, um typ-spezifische Menüpunkte zu zeigen (#8).
 */
const contextMenuBlock = computed<Block | null>(() =>
  contextMenu.value.blockId
    ? children.value.find((b) => b.id === contextMenu.value.blockId) ?? null
    : null,
)

async function openContextMenu(e: MouseEvent, block: Block): Promise<void> {
  contextMenu.value = { visible: true, x: e.clientX, y: e.clientY, blockId: block.id }

  // #5 – Nach dem Render die tatsächliche Größe des Menüs auslesen und
  // die Position an den Viewport klemmen, damit das Menü nie abgeschnitten wird.
  await nextTick()
  if (contextMenuEl.value) {
    const w = contextMenuEl.value.offsetWidth
    const h = contextMenuEl.value.offsetHeight
    const vw = window.innerWidth
    const vh = window.innerHeight
    contextMenu.value = {
      ...contextMenu.value,
      x: Math.min(Math.max(8, e.clientX), vw - w - 8),
      y: Math.min(Math.max(8, e.clientY), vh - h - 8),
    }
  }

  // Defer so this click doesn't immediately trigger closeContextMenu.
  setTimeout(() => {
    document.addEventListener('click', closeContextMenu, { once: true })
    document.addEventListener('keydown', onContextMenuKeydown, { once: true })
  }, 0)
}

function closeContextMenu(): void {
  document.removeEventListener('keydown', onContextMenuKeydown)
  contextMenu.value = { visible: false, x: 0, y: 0, blockId: null }
}

function onContextMenuKeydown(e: KeyboardEvent): void {
  if (e.key === 'Escape') closeContextMenu()
}

// ── Block colors (#26) ────────────────────────────────────────────────────────

function blockColorStyle(block: Block): Record<string, string> {
  const style: Record<string, string> = {}
  const bg = resolveBgColor(block.content?.bgColor as string | undefined)
  if (bg) { style['background-color'] = bg; style['border-radius'] = '3px' }
  const color = resolveColor(block.content?.color as string | undefined)
  if (color) style['--block-text-color'] = color
  return style
}

async function setBlockColor(key: string): Promise<void> {
  const id = contextMenu.value.blockId
  closeContextMenu()
  if (!id) return
  const block = children.value.find((b) => b.id === id)
  if (!block) return
  await blockStore.updateBlock(id, {
    content: { ...(block.content ?? {}), color: key === 'default' ? undefined : key },
  })
}

async function setBlockBgColor(key: string): Promise<void> {
  const id = contextMenu.value.blockId
  closeContextMenu()
  if (!id) return
  const block = children.value.find((b) => b.id === id)
  if (!block) return
  await blockStore.updateBlock(id, {
    content: { ...(block.content ?? {}), bgColor: key === 'default' ? undefined : key },
  })
}

// #100 – Toggle conversion in context menu (headings, paragraph, text_toggle)
async function contextMenuConvertToggle(): Promise<void> {
  const id = contextMenu.value.blockId
  closeContextMenu()
  if (!id) return
  const block = children.value.find((b) => b.id === id)
  if (!block || !isToggleConvertible(block.type)) return

  let newType: string
  if (block.type === 'paragraph') {
    newType = 'text_toggle'
  } else if (block.type === 'text_toggle') {
    newType = 'paragraph'
  } else if (isToggleHeadingType(block.type)) {
    newType = block.type.replace('_toggle', '')
  } else {
    // Static heading → toggle heading
    newType = block.type + '_toggle'
  }
  await blockStore.updateBlock(id, { type: newType })
}

async function contextMenuDuplicate(): Promise<void> {
  const id = contextMenu.value.blockId
  closeContextMenu()
  if (!id) return
  const block = children.value.find((b) => b.id === id)
  if (block) await duplicateBlock(block)
}

async function contextMenuDelete(): Promise<void> {
  const id = contextMenu.value.blockId
  const block = id ? children.value.find((b) => b.id === id) : null
  closeContextMenu()
  if (!id) return
  // Drive-Blöcke haben physische Dateien — Bestätigung erforderlich.
  if (block?.type === 'drive') {
    openDriveDeleteConfirm(id)
  } else {
    await onDeleteSelf(id)
  }
}

// #8 – Inline toggle for database blocks in context menu
function contextMenuToggleInline(): void {
  const id = contextMenu.value.blockId
  closeContextMenu()
  if (id) toggleDbInline(id)
}

// #11 – Add column for layout blocks in context menu
const LAYOUT_MAX_COLUMNS = 4

/**
 * Returns the number of active column children for a given layout block ID.
 * Used by the context menu to conditionally show the "Add column" item.
 */
function layoutColumnCount(blockId: string): number {
  return blockStore
    .getChildren(blockId)
    .filter((b) => b.state === 'active' && b.type === 'column')
    .length
}

async function contextMenuAddColumn(): Promise<void> {
  const id = contextMenu.value.blockId
  closeContextMenu()
  if (!id) return
  const block = children.value.find((b) => b.id === id)
  if (!block || block.type !== 'layout') return

  const cols = blockStore
    .getChildren(id)
    .filter((b) => b.state === 'active' && b.type === 'column')
    .sort((a, b) => a.position - b.position)

  if (cols.length >= LAYOUT_MAX_COLUMNS) return

  const pos = (cols[cols.length - 1]?.position ?? 0) + 1
  await blockStore.createBlock({
    type: 'column',
    parent_id: id,
    position: pos,
    content: {},
  })
  await blockStore.fetchChildren(id, true)
  await nextTick()
  const n = blockStore
    .getChildren(id)
    .filter((b) => b.state === 'active' && b.type === 'column')
    .length
  await blockStore.updateBlock(id, {
    content: { ...(block.content ?? {}), widths: Array(n).fill(1 / n) },
  })
}

// ── Synched mirror lock toggle ────────────────────────────────────────────────

/**
 * Toggle the per-block lock state of a synched_mirror block.
 * When locked, the mirror is read-only (pointer-events disabled inside it).
 * The lock flag is persisted in block.content.locked via a PATCH to the backend.
 */
async function contextMenuToggleMirrorLock(): Promise<void> {
  const id = contextMenu.value.blockId
  closeContextMenu()
  if (!id) return
  const block = children.value.find((b) => b.id === id)
  if (!block || block.type !== 'synched_mirror') return
  const currentlyLocked = (block.content?.locked as boolean | undefined) ?? false
  await blockStore.updateBlock(id, {
    content: { ...(block.content ?? {}), locked: !currentlyLocked },
  })
}

// ── Block type icon map (used in context menu header) ─────────────────────────

const BLOCK_TYPE_ICONS: Record<string, string> = {
  paragraph: 'mdi:text',
  text_toggle: 'mdi:chevron-right-circle-outline',
  heading_1: 'mdi:format-header-1',
  heading_2: 'mdi:format-header-2',
  heading_3: 'mdi:format-header-3',
  heading_4: 'mdi:format-header-4',
  heading_1_toggle: 'mdi:chevron-right-box-outline',
  heading_2_toggle: 'mdi:chevron-right-box-outline',
  heading_3_toggle: 'mdi:chevron-right-box-outline',
  heading_4_toggle: 'mdi:chevron-right-box-outline',
  bulleted_list_item: 'mdi:format-list-bulleted',
  numbered_list_item: 'mdi:format-list-numbered',
  to_do: 'mdi:checkbox-blank-outline',
  toggle: 'mdi:chevron-right-box-outline',
  quote: 'mdi:format-quote-open',
  callout: 'mdi:lightbulb-outline',
  divider: 'mdi:minus',
  image: 'mdi:image-outline',
  video: 'mdi:video-outline',
  audio: 'mdi:music-note-outline',
  pdf: 'mdi:file-pdf-box',
  file: 'mdi:file-upload-outline',
  drive: 'mdi:folder-outline',
  bookmark: 'mdi:bookmark-outline',
  embed: 'mdi:code-tags',
  table_of_contents: 'mdi:format-list-bulleted',
  layout: 'mdi:view-column-outline',
  synched_origin: 'mdi:sync',
  synched_mirror: 'mdi:sync',
  page: 'mdi:file-outline',
  database: 'mdi:table-large',
  linked_database: 'mdi:table-arrow-right',
}

function blockTypeIconFor(type: string): string {
  return BLOCK_TYPE_ICONS[type] ?? 'mdi:square-outline'
}

// ── Navigate to synched_mirror origin (Ctrl+MiddleClick on drag handle) ───────

/**
 * Walk the parent_id chain from *startId* upward until a block of type
 * 'page' is found.  Fetches unknown blocks on demand.  Returns null if no
 * page ancestor is reachable within a reasonable depth.
 */
async function findPageAncestor(startId: string | null): Promise<string | null> {
  let id = startId
  for (let depth = 0; depth < 16 && id; depth++) {
    let b = blockStore.blocks[id]
    if (!b) {
      try { b = await blockStore.fetchBlock(id) } catch { return null }
    }
    if (b.type === 'page') return b.id
    id = b.parent_id ?? null
  }
  return null
}

async function navigateToOrigin(block: Block): Promise<void> {
  const originId = block.reference_id
  if (!originId) return

  // Ensure origin block is cached.
  let origin = blockStore.blocks[originId]
  if (!origin) {
    try { origin = await blockStore.fetchBlock(originId) } catch { return }
  }

  const pageId = await findPageAncestor(origin.parent_id ?? null)
  if (!pageId) return

  const currentPageId = router.currentRoute.value.params.id as string | undefined

  if (pageId === currentPageId) {
    // Same page: scroll the synched_origin block into view.
    await nextTick()
    document.querySelector<HTMLElement>(`[data-block-id="${originId}"]`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  } else {
    // Different page: navigate there, then scroll after the view renders.
    await router.push(`/blocks/${pageId}`)
    setTimeout(() => {
      document.querySelector<HTMLElement>(`[data-block-id="${originId}"]`)
        ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }, 400)
  }
}

// ── Drag handle mouse-down dispatcher ─────────────────────────────────────────

/**
 * Handles non-left-button presses on the drag handle.
 * Currently: Ctrl+MiddleClick on a synched_mirror → navigate to its origin.
 */
function onHandleMouseDown(e: MouseEvent, block: Block): void {
  // Middle button = 1. Ignore all other buttons (left/right handled elsewhere).
  if (e.button !== 1) return
  if (!(e.ctrlKey || e.metaKey)) return
  if (block.type !== 'synched_mirror') return
  // Prevent browser autoscroll triggered by middle-button press.
  e.preventDefault()
  navigateToOrigin(block)
}

// ── Drag handle click dispatcher ──────────────────────────────────────────────

/**
 * Set to true during an active drag and reset asynchronously after dragend,
 * so the click event that some browsers fire on mouseup after a drag gesture
 * can be detected and suppressed in onHandleClick.
 */
let _dragInProgress = false

function onHandleClick(e: MouseEvent, block: Block): void {
  // Suppress clicks that directly follow a drag gesture.
  if (_dragInProgress) return
  e.stopPropagation()

  const ctrl = e.ctrlKey || e.metaKey
  const shift = e.shiftKey
  const alt = e.altKey

  // Ctrl+Shift+Click → duplicate
  if (ctrl && shift) {
    duplicateBlock(block)
    return
  }
  // Ctrl+Alt+Click → delete
  if (ctrl && alt) {
    onDeleteSelf(block.id)
    return
  }
  // Ctrl+Click → insert empty block below
  if (ctrl) {
    insertEmptyBlockBelow(block)
    return
  }
  // Shift+Click → insert empty block above
  if (shift) {
    insertEmptyBlockAbove(block)
    return
  }

  // Plain click → context menu
  openContextMenu(e, block)
}

// ── Drag & Drop ───────────────────────────────────────────────────────────────

interface DropState {
  above: boolean
  below: boolean
  on: boolean
}

const dropStates = ref<Record<string, DropState>>({})

function getDropState(id: string): DropState {
  return dropStates.value[id] ?? { above: false, below: false, on: false }
}

function setDropState(id: string, state: DropState): void {
  dropStates.value[id] = state
}

function clearDropState(id: string): void {
  delete dropStates.value[id]
}

/** Whether a dragged block is hovering over the end-of-list drop zone. */
const dropAtEnd = ref(false)

function onDragStart(e: DragEvent, block: Block): void {
  // (#10) Restrict drag initiation to the dedicated handle element for all
  // block types. draggable="true" now lives on the handle span (not the outer
  // div), so legitimate drags already arrive with e.target inside the handle.
  // This guard is a safety net for any stray dragstart that still bubbles
  // (e.g. from a nested draggable child or browser image-drag behaviour).
  // Critically, e.preventDefault() cancels the browser drag gesture for
  // non-handle clicks — without it the browser would still initiate a ghost
  // drag that swallows the mousedown and prevents native textarea cursor
  // placement from working.
  const handle = (e.target as HTMLElement).closest?.('.content-block__drag-handle')
  if (!handle) {
    e.preventDefault()
    return
  }

  // Stop the event from bubbling into a parent BlockContentSection (e.g. a
  // toggle block's ancestor level). Without this, the parent level would call
  // startDrag again with the wrong blockId, overwriting the correct state.
  e.stopPropagation()

  _dragInProgress = true
  e.dataTransfer!.effectAllowed = 'move'
  drag.startDrag(block.id, props.parentId, block.type)
}

function onDragEnd(block: Block): void {
  drag.endDrag()
  clearDropState(block.id)
  dropAtEnd.value = false
  // Defer so onHandleClick can read the flag before it is cleared.
  setTimeout(() => { _dragInProgress = false }, 0)
}

function onDragOver(e: DragEvent, block: Block): void {
  const { blockId, blockType, dragMode } = drag.getDragging()
  // Ignore column-mode drags — they are handled by LayoutBlock.
  if (!blockId || dragMode === 'column') return

  e.preventDefault()
  // Stop bubbling so that hovering over a block inside a toggle does not
  // accidentally activate drop states on the surrounding parent-level blocks.
  e.stopPropagation()
  e.dataTransfer!.dropEffect = 'move'

  // Page blocks in Block Editor: may only be dropped ON other pages (as children).
  // Above/below (sibling insertion) is not permitted for page blocks here.
  if (blockType === 'page') {
    if (block.type !== 'page') {
      clearDropState(block.id)
      return
    }
    setDropState(block.id, { above: false, below: false, on: true })
    return
  }

  // For all other block types: determine above / below by cursor position.
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
  const mid = rect.top + rect.height / 2
  if (e.clientY < mid) {
    setDropState(block.id, { above: true, below: false, on: false })
  } else {
    setDropState(block.id, { above: false, below: true, on: false })
  }
}

async function onDrop(e: DragEvent, block: Block): Promise<void> {
  const { blockId, dragMode } = drag.getDragging()
  // Ignore column-mode drags.
  if (!blockId || dragMode === 'column') return

  e.preventDefault()
  e.stopPropagation()

  const siblings = children.value
  const idx = siblings.findIndex((b) => b.id === block.id)
  const state = getDropState(block.id)

  if (state.on) {
    await drag.dropOnBlock(block.id)
  } else if (state.above) {
    const before = idx > 0 ? siblings[idx - 1].position : null
    await drag.dropBetween(props.parentId, before, block.position)
  } else {
    const after =
      idx < siblings.length - 1 ? siblings[idx + 1].position : null
    await drag.dropBetween(props.parentId, block.position, after)
  }

  clearDropState(block.id)
}

// Drop into the empty-state zone (e.g. a toggle with no children yet).
function onDragOverEmpty(e: DragEvent): void {
  const { blockId, dragMode } = drag.getDragging()
  if (!blockId || dragMode === 'column') return
  e.preventDefault()
  e.stopPropagation()
  e.dataTransfer!.dropEffect = 'move'
  dropAtEnd.value = true
}

function onDragLeaveEmpty(): void {
  dropAtEnd.value = false
}

async function onDropEmpty(e: DragEvent): Promise<void> {
  e.preventDefault()
  e.stopPropagation()
  dropAtEnd.value = false
  const { blockId, dragMode } = drag.getDragging()
  if (!blockId || dragMode === 'column') return
  // Insert as the sole child.
  await drag.dropBetween(props.parentId, null, null)
}

// Drop at the end of the list (the click-to-add affordance area).
function onDragOverEnd(e: DragEvent): void {
  const { blockId, dragMode } = drag.getDragging()
  if (!blockId || dragMode === 'column') return
  e.preventDefault()
  e.stopPropagation()
  e.dataTransfer!.dropEffect = 'move'
  dropAtEnd.value = true
}

function onDragLeaveEnd(): void {
  dropAtEnd.value = false
}

async function onDropEnd(e: DragEvent): Promise<void> {
  e.preventDefault()
  e.stopPropagation()
  dropAtEnd.value = false
  const { blockId, dragMode } = drag.getDragging()
  if (!blockId || dragMode === 'column') return
  const last = children.value[children.value.length - 1]
  await drag.dropBetween(props.parentId, last?.position ?? null, null)
}
</script>

<template>
  <div
    class="block-content"
    :class="{ 'block-content--nested': nested }"
  >
    <!-- Context menu (rendered at root level so it is never clipped) -->
    <Teleport to="body">
      <div
        v-if="contextMenu.visible"
        ref="contextMenuEl"
        class="block-context-menu"
        :style="{ top: contextMenu.y + 'px', left: contextMenu.x + 'px' }"
        @click.stop
      >
        <!-- Block type identification label (always topmost) -->
        <div v-if="contextMenuBlock" class="block-context-menu__type-label">
          <Icon :icon="blockTypeIconFor(contextMenuBlock.type)" width="13" height="13" />
          {{ t(`block.types.${contextMenuBlock.type}`) }}
        </div>
        <div v-if="contextMenuBlock" class="block-context-menu__divider" />

        <!-- #8 – Inline toggle: database blocks only -->
        <button
          v-if="contextMenuBlock?.type === 'database'"
          class="block-context-menu__item"
          @click="contextMenuToggleInline"
        >
          <Icon
            :icon="isDbInline(contextMenu.blockId!) ? 'mdi:table-off' : 'mdi:table'"
            width="14"
            height="14"
          />
          {{ isDbInline(contextMenu.blockId!) ? t('block.contextMenu.openFullPage') : t('block.contextMenu.showInline') }}
        </button>

        <!-- #100 – Toggle conversion: headings, paragraph, text_toggle -->
        <button
          v-if="contextMenuBlock && isToggleConvertible(contextMenuBlock.type)"
          class="block-context-menu__item"
          @click="contextMenuConvertToggle"
        >
          <Icon
            :icon="isToggleHeadingType(contextMenuBlock.type) || contextMenuBlock.type === 'text_toggle'
              ? 'mdi:format-header-pound'
              : 'mdi:chevron-right-box-outline'"
            width="14"
            height="14"
          />
          {{ isToggleHeadingType(contextMenuBlock.type) || contextMenuBlock.type === 'text_toggle'
            ? t('block.contextMenu.convertToStatic')
            : t('block.contextMenu.convertToToggle') }}
        </button>

        <!-- Lock toggle: synched_mirror blocks only -->
        <button
          v-if="contextMenuBlock?.type === 'synched_mirror'"
          class="block-context-menu__item"
          @click="contextMenuToggleMirrorLock"
        >
          <Icon
            :icon="(contextMenuBlock.content?.locked) ? 'mdi:lock-open-outline' : 'mdi:lock-outline'"
            width="14"
            height="14"
          />
          {{ (contextMenuBlock.content?.locked) ? t('block.contextMenu.unlockMirror') : t('block.contextMenu.lockMirror') }}
        </button>

        <!-- #11 – Add column: layout blocks below maximum column count only -->
        <button
          v-if="contextMenuBlock?.type === 'layout' && layoutColumnCount(contextMenuBlock.id) < LAYOUT_MAX_COLUMNS"
          class="block-context-menu__item"
          @click="contextMenuAddColumn"
        >
          <Icon icon="mdi:table-column-plus-after" width="14" height="14" />
          {{ t('layout.addColumn') }}
        </button>

        <button class="block-context-menu__item" @click="contextMenuDuplicate">
          <Icon icon="mdi:content-copy" width="14" height="14" />
          {{ t('actions.duplicate') }}
        </button>

        <!-- Color picker (#26) – alle Blöcke außer Divider und inline Datenbanken -->
        <template v-if="contextMenuBlock
          && contextMenuBlock.type !== 'divider'
          && contextMenuBlock.type !== 'linked_database'
          && !(contextMenuBlock.type === 'database' && isDbInline(contextMenu.blockId!))"
        >
          <div class="block-context-menu__divider" />
          <div class="block-context-menu__color-section">
            <span class="block-context-menu__color-label">{{ t('block.contextMenu.textColor') }}</span>
            <div class="block-context-menu__color-dots">
              <button
                v-for="(hex, key) in BLOCK_COLORS"
                :key="key"
                class="block-context-menu__color-dot"
                :class="{ 'block-context-menu__color-dot--active': (contextMenuBlock.content?.color ?? 'default') === key }"
                :style="hex ? { background: hex } : { background: 'var(--color-text)', opacity: '0.35' }"
                :title="key"
                @click.stop="setBlockColor(key)"
              />
            </div>
            <span class="block-context-menu__color-label">{{ t('block.contextMenu.backgroundColor') }}</span>
            <div class="block-context-menu__color-dots">
              <button
                v-for="(hex, key) in BLOCK_BG_COLORS"
                :key="key"
                class="block-context-menu__color-dot"
                :class="{ 'block-context-menu__color-dot--active': (contextMenuBlock.content?.bgColor ?? 'default') === key }"
                :style="hex ? { background: hex } : { background: 'var(--color-border)', opacity: '1' }"
                :title="key"
                @click.stop="setBlockBgColor(key)"
              />
            </div>
          </div>
          <div class="block-context-menu__divider" />
        </template>

        <button class="block-context-menu__item block-context-menu__item--danger" @click="contextMenuDelete">
          <Icon icon="mdi:trash-can-outline" width="14" height="14" />
          {{ t('actions.delete') }}
        </button>
      </div>
    </Teleport>

    <!-- Drive-Block Lösch-Bestätigung -->
    <Teleport to="body">
      <div
        v-if="driveDeleteConfirmId"
        class="drive-delete-overlay"
        @click.self="closeDriveDeleteConfirm"
      >
        <div class="drive-delete-modal">
          <div class="drive-delete-modal__icon">
            <Icon icon="mdi:alert-outline" width="22" height="22" />
          </div>
          <div class="drive-delete-modal__body">
            <p class="drive-delete-modal__title">{{ t('block.contextMenu.driveDeleteTitle') }}</p>
            <p class="drive-delete-modal__desc">
              {{ t('block.contextMenu.driveDeleteDesc') }}
            </p>
          </div>
          <div class="drive-delete-modal__actions">
            <button class="drive-delete-modal__btn drive-delete-modal__btn--cancel" @click="closeDriveDeleteConfirm">
              {{ t('actions.cancel') }}
            </button>
            <button class="drive-delete-modal__btn drive-delete-modal__btn--confirm" @click="confirmDriveDelete">
              {{ t('actions.delete') }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <div
      v-if="isEmpty"
      class="block-content__empty"
      :class="{ 'block-content__empty--drop-active': dropAtEnd }"
      @dragover="onDragOverEmpty"
      @dragleave="onDragLeaveEmpty"
      @drop="onDropEmpty"
    >
      <!-- Template picker: shown when this section belongs to a database entry
           and the owning database has at least one template defined. -->
      <template v-if="databaseId && templateStore.getTemplates(databaseId).length > 0">
        <div class="block-content__template-hint">
          {{ t('db.templates.emptyHint') }}
        </div>
        <div class="block-content__template-list">
          <button
            v-for="tmpl in templateStore.getTemplates(databaseId)"
            :key="tmpl.id"
            class="block-content__template-btn"
            @click.stop="applyTemplate(tmpl.id)"
          >
            <Icon
              :icon="tmpl.icon ?? 'mdi:file-document-outline'"
              width="13"
              height="13"
            />
            {{ (tmpl.content?.title as string | undefined) || t('db.templates.untitled') }}
          </button>
          <button class="block-content__template-btn block-content__template-btn--add" @click="onAddBlock">
            {{ t('main.noContent') }}
          </button>
        </div>
      </template>
      <template v-else>
        <span @click="onAddBlock">{{ t('main.noContent') }}</span>
      </template>
    </div>

    <!--
      Outer <template> with v-for so that each block can be followed by its
      optional toggle-children section without an extra wrapper element.
    -->
    <template v-for="block in children" :key="block.id">
      <div
        class="content-block"
        :class="{
          'content-block--text': isTextBlock(block),
          'content-block--divider': block.type === 'divider',
          'content-block--quote': block.type === 'quote',
          'content-block--callout': block.type === 'callout',
          'content-block--media': isMediaBlock(block),
          'content-block--layout': isLayoutBlock(block) || isSynchedBlock(block),
          'content-block--drop-above': getDropState(block.id).above,
          'content-block--drop-below': getDropState(block.id).below,
          'content-block--drop-on': getDropState(block.id).on,
        }"
        :data-block-id="block.id"
        :style="blockColorStyle(block)"
        @dragstart="(e) => onDragStart(e, block)"
        @dragend="() => onDragEnd(block)"
        @dragover="(e) => onDragOver(e, block)"
        @dragleave="() => clearDropState(block.id)"
        @drop="(e) => onDrop(e, block)"
      >
        <!-- Drag handle — click opens context menu / modifier shortcuts.
             (#10) draggable="true" is now on the handle span for ALL block
             types. Previously the outer div carried draggable for non-layout
             blocks, which caused the browser to intercept clicks anywhere on
             the block as a potential drag, preventing native textarea cursor
             placement. With draggable scoped to the handle, only deliberate
             handle interactions start a drag. -->
        <span
          class="content-block__drag-handle"
          draggable="true"
          @mousedown="(e) => onHandleMouseDown(e, block)"
          @click="(e) => onHandleClick(e, block)"
        >
          <Icon icon="mdi:drag" width="14" height="14" />
        </span>

        <!-- ── Divider ──────────────────────────────────────────────────── -->
        <template v-if="block.type === 'divider'">
          <hr class="content-block__divider" />
        </template>

        <!-- ── Text block ──────────────────────────────────────────────── -->
        <template v-else-if="isTextBlock(block)">
          <BlockEditorRow
            :block="block"
            :parent-id="parentId"
            :focus-requested="pendingFocusBlockId === block.id"
            :list-index="listIndexFor(block)"
            :toggle-open="getToggleOpen(block)"
            @create-after="onCreateAfter"
            @create-linked-db="onCreateLinkedDb"
            @delete-self="onDeleteSelf"
            @navigate="onNavigate"
            @focus-consumed="onFocusConsumed"
            @toggle-open="onToggleOpen"
          />
        </template>

        <!-- ── Media / file / meta block ───────────────────────────────── -->
        <template v-else-if="isMediaBlock(block)">
          <component
            :is="mediaComponentFor(block.type)"
            :block="block"
            :parent-id="parentId"
          />
        </template>

        <!-- ── Layout block (Tier 3) ────────────────────────────────────── -->
        <template v-else-if="isLayoutBlock(block)">
          <LayoutBlock
            :block="block"
            :parent-id="parentId"
          />
        </template>

        <!-- ── Synched origin block ──────────────────────────────────────── -->
        <template v-else-if="block.type === 'synched_origin'">
          <SynchedOriginBlock
            :block="block"
            :parent-id="parentId"
          />
        </template>

        <!-- ── Synched mirror block ──────────────────────────────────────── -->
        <template v-else-if="block.type === 'synched_mirror'">
          <SynchedMirrorBlock
            :block="block"
            :parent-id="parentId"
          />
        </template>

        <!-- ── Structural / link block ──────────────────────────────────── -->
        <template v-else>
          <span class="content-block__icon">
            <Icon :icon="iconFor(block)" width="16" height="16" />
          </span>

          <span
            class="content-block__label"
            :class="{ 'content-block__label--link': block.type === 'page' || block.type === 'database' }"
            @click="onBlockLinkClick(block)"
          >
            {{ labelFor(block) }}
          </span>

        </template>
      </div>

      <!-- Toggle children — rendered outside the block wrapper so they can
           fill their own indented section without affecting the block row.
           All toggle types (toggle, text_toggle, heading_N_toggle) store their
           children as real DB blocks and use the same nested section. -->
      <div
        v-if="ALL_TOGGLE_TYPES.has(block.type) && (toggleOpenStates[block.id] ?? false)"
        class="content-block__toggle-children"
        @dragover.stop
        @drop.stop
      >
        <BlockContentSection :parent-id="block.id" :nested="true" />
      </div>

      <!-- Inline view for linked_database blocks — always rendered directly below
           the block row. The referenced DB is shown via its own DatabaseBlock
           (inline mode). When reference_id is null (target DB deleted), nothing
           is rendered here; the row label already signals the broken link. -->
      <div
        v-if="block.type === 'linked_database' && block.reference_id"
        class="content-block__db-inline"
        @dragover.stop
        @drop.stop
      >
        <DatabaseBlock :block-id="block.reference_id" :inline="true" />
      </div>

      <!-- #8 – Inline database view below the block row.
           Shown when the user chose "Show inline" in the context menu.
           The row (icon + label + navigation) is preserved. -->
      <div
        v-if="block.type === 'database' && isDbInline(block.id)"
        class="content-block__db-inline"
        @dragover.stop
        @drop.stop
      >
        <DatabaseBlock :block-id="block.id" :inline="true" />
      </div>
    </template>

    <!-- Click-to-add affordance at the bottom of the content area.
         Also serves as a drop zone for appending dragged blocks at the end. -->
    <div
      v-if="!isEmpty"
      class="block-content__add-area"
      :class="{ 'block-content__add-area--drop-active': dropAtEnd }"
      @click="onAddBlock"
      @dragover="onDragOverEnd"
      @dragleave="onDragLeaveEnd"
      @drop="onDropEnd"
    />
  </div>
</template>

<style scoped>
.block-content {
  flex: 1;
  max-width: 720px;
  margin: 0 auto;
  padding: 1rem 3rem 4rem;
  width: 100%;
}

.block-content--nested {
  padding: 0.125rem 0 0;
  max-width: none;
  margin: 0;
}

.block-content--nested .block-content__add-area {
  min-height: 0.5rem;
}

.block-content--nested .block-content__empty {
  padding-top: 0;
  min-height: 1.5rem;
}

.block-content__empty {
  padding-top: 1rem;
  font-size: 0.875rem;
  color: var(--color-text-muted);
  cursor: text;
  min-height: 2rem;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  border-radius: 6px;
  transition: background 0.1s;
}

.block-content__empty--drop-active {
  background: var(--color-accent-subtle);
  outline: 1.5px dashed var(--color-accent);
  outline-offset: -1px;
}

/* ── Template picker (shown when templates exist for the parent database) ── */

.block-content__template-hint {
  font-size: 0.775rem;
  color: var(--color-text-muted);
  margin-bottom: 6px;
}

.block-content__template-list {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  align-items: center;
}

.block-content__template-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 9px;
  border: 1px solid var(--color-border);
  border-radius: 5px;
  background: var(--color-surface);
  font-size: 0.775rem;
  color: var(--color-text-muted);
  cursor: pointer;
  transition: background 0.1s, color 0.1s, border-color 0.1s;
  white-space: nowrap;
}

.block-content__template-btn:hover {
  background: var(--color-hover);
  color: var(--color-text);
  border-color: var(--color-border-strong, var(--color-border));
}

.block-content__template-btn--add {
  border-style: dashed;
  color: var(--color-text-muted);
}

/* ── Add area ─────────────────────────────────────────────────────────────── */
.block-content__add-area {
  min-height: 4rem;
  cursor: text;
  border-radius: 6px;
  transition: background 0.1s;
}

.block-content__add-area--drop-active {
  background: var(--color-accent-subtle);
  outline: 1.5px dashed var(--color-accent);
  outline-offset: -1px;
}

/* ── Content block wrapper ───────────────────────────────────────────────── */
.content-block {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 2px 6px;
  border-radius: 6px;
  margin-bottom: 1px;
  position: relative;
  transition: background 0.1s;
}

.content-block:not(.content-block--text):not(.content-block--divider):not(.content-block--media):not(.content-block--layout) {
  cursor: grab;
  align-items: center;
  padding: 4px 6px;
}

.content-block:not(.content-block--text):not(.content-block--divider):not(.content-block--media):not(.content-block--layout):hover {
  background: var(--color-hover);
}

.content-block--text {
  cursor: default;
}

.content-block--text:hover {
  background: var(--color-hover);
}

/* ── Media block ─────────────────────────────────────────────────────────── */
.content-block--media {
  cursor: default;
  align-items: flex-start;
  padding: 4px 6px;
  margin-bottom: 4px;
}

.content-block--media:hover {
  background: var(--color-hover);
}

/* ── Layout block (Tier 3) ───────────────────────────────────────────────── */
.content-block--layout {
  cursor: default;
  align-items: flex-start;
  padding: 4px 6px;
  margin-bottom: 6px;
}

.content-block--layout:hover {
  background: var(--color-hover);
}

/* ── Divider block ───────────────────────────────────────────────────────── */
.content-block--divider {
  align-items: center;
  padding: 6px 6px;
}

.content-block__divider {
  flex: 1;
  border: none;
  border-top: 1px solid var(--color-border);
  margin: 0;
}

/* Remove the top-padding from the drag handle when inside a divider row. */
.content-block--divider .content-block__drag-handle {
  padding-top: 0;
}

/* ── Quote block ─────────────────────────────────────────────────────────── */
.content-block--quote {
  border-left: 3px solid var(--color-accent);
  padding-left: 10px;
  border-radius: 0 6px 6px 0;
}

/* ── Callout block ───────────────────────────────────────────────────────── */
.content-block--callout {
  background: var(--color-hover);
  border-radius: 6px;
  padding: 6px 10px;
}

.content-block--callout:hover {
  background: var(--color-active);
}

/* ── Toggle children ─────────────────────────────────────────────────────── */
.content-block__toggle-children {
  padding-left: 1.5rem;
  border-left: 2px solid var(--color-border);
  margin-left: 1.75rem;
  margin-bottom: 2px;
}

/* ── #8 – Inline-Datenbankcontainer ─────────────────────────────────────── */
/*
 * DELIBERATE DESIGN — no outer border (do not revert):
 * The inline database table renders without an outer border so it integrates
 * flush into the surrounding content block. The DatabaseBlock's own internal
 * table borders (cell grid lines) provide all necessary structural cues.
 * Removing the wrapper border produces a slimmer, calmer appearance and
 * enables future styling choices at the block level without double borders.
 */
.content-block__db-inline {
  margin-bottom: 8px;
  overflow: auto;
  max-height: 520px;
}

/* ── Drop states ─────────────────────────────────────────────────────────── */
.content-block--drop-on {
  background: var(--color-accent-subtle);
  outline: 1.5px dashed var(--color-accent);
  outline-offset: -1px;
}

.content-block--drop-above {
  border-top: 2px solid var(--color-accent);
}

.content-block--drop-below {
  border-bottom: 2px solid var(--color-accent);
}

/* ── Drag handle ─────────────────────────────────────────────────────────── */
.content-block__drag-handle {
  color: var(--color-text-muted);
  opacity: 0;
  flex-shrink: 0;
  transition: opacity 0.1s;
  cursor: grab;
  display: flex;
  align-items: center;
  padding-top: 4px;
  /* Reset button styles when rendered as interactive element */
  background: none;
  border: none;
  padding-left: 0;
  padding-right: 0;
  font: inherit;
}

.content-block:hover .content-block__drag-handle {
  opacity: 1;
}

.content-block__drag-handle:hover {
  color: var(--color-text);
}

/* ── Structural block internals ──────────────────────────────────────────── */
.content-block__icon {
  flex-shrink: 0;
  color: var(--color-text-muted);
  display: flex;
  align-items: center;
}

.content-block__label {
  flex: 1;
  font-size: 0.875rem;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.content-block__label--link {
  cursor: pointer;
}

.content-block__label--link:hover {
  text-decoration: underline;
  color: var(--color-accent);
}

/* ── Print ───────────────────────────────────────────────────────────────── */
@media print {
  .block-content__empty,
  .block-content__add-area {
    display: none !important;
  }

  .content-block__drag-handle {
    display: none !important;
  }

  .content-block__db-inline {
    overflow: visible !important;
    max-height: none !important;
  }
}
</style>

<!-- Context menu styles are unscoped so Teleport renders them correctly. -->
<style>
.block-context-menu {
  position: fixed;
  z-index: 9999;
  min-width: 180px;
  background: var(--color-surface, #fff);
  border: 1px solid var(--color-border, #e0e0e0);
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
  padding: 4px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.block-context-menu__type-label {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px 3px;
  font-size: 0.72rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--color-text-muted);
  user-select: none;
}

.block-context-menu__item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 6px 10px;
  border: none;
  border-radius: 5px;
  background: none;
  font-size: 0.875rem;
  color: var(--color-text, #1a1a1a);
  cursor: pointer;
  text-align: left;
  transition: background 0.1s;
}

.block-context-menu__item:hover {
  background: var(--color-hover, rgba(0, 0, 0, 0.05));
}

.block-context-menu__divider {
  height: 1px;
  background: var(--color-border);
  margin: 3px 0;
}

.block-context-menu__color-section {
  padding: 6px 8px 4px;
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.block-context-menu__color-label {
  font-size: 0.68rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-text-muted);
}

.block-context-menu__color-dots {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}

.block-context-menu__color-dot {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  border: 2px solid transparent;
  cursor: pointer;
  padding: 0;
  transition: transform 0.1s, box-shadow 0.1s;
  flex-shrink: 0;
}

.block-context-menu__color-dot:hover {
  transform: scale(1.2);
}

.block-context-menu__color-dot--active {
  box-shadow: 0 0 0 2px var(--color-accent);
}

.block-context-menu__item--danger {
  color: var(--color-danger, #d32f2f);
}

.block-context-menu__item--danger:hover {
  background: var(--color-danger-subtle, rgba(211, 47, 47, 0.07));
}

/* ── Drive-Block Lösch-Bestätigung ───────────────────────────────────────── */
.drive-delete-overlay {
  position: fixed;
  inset: 0;
  z-index: 10000;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
}

.drive-delete-modal {
  background: var(--color-surface, #fff);
  border: 1px solid var(--color-border, #e0e0e0);
  border-radius: 10px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
  padding: 24px 24px 20px;
  width: 340px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.drive-delete-modal__icon {
  color: var(--color-danger, #d32f2f);
  display: flex;
}

.drive-delete-modal__body {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.drive-delete-modal__title {
  margin: 0;
  font-size: 0.9375rem;
  font-weight: 600;
  color: var(--color-text, #1a1a1a);
}

.drive-delete-modal__desc {
  margin: 0;
  font-size: 0.8125rem;
  color: var(--color-text-muted, #666);
  line-height: 1.45;
}

.drive-delete-modal__actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 4px;
}

.drive-delete-modal__btn {
  padding: 6px 14px;
  border-radius: 6px;
  font-size: 0.875rem;
  font-family: inherit;
  cursor: pointer;
  border: 1px solid var(--color-border, #e0e0e0);
  transition: background 0.1s, color 0.1s;
}

.drive-delete-modal__btn--cancel {
  background: var(--color-surface, #fff);
  color: var(--color-text, #1a1a1a);
}

.drive-delete-modal__btn--cancel:hover {
  background: var(--color-hover, rgba(0,0,0,0.05));
}

.drive-delete-modal__btn--confirm {
  background: var(--color-danger, #d32f2f);
  color: #fff;
  border-color: var(--color-danger, #d32f2f);
}

.drive-delete-modal__btn--confirm:hover {
  background: #b71c1c;
  border-color: #b71c1c;
}
</style>

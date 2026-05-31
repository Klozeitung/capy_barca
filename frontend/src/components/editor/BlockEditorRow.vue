<script setup lang="ts">
/**
 * BlockEditorRow
 *
 * Renders a single text-type block as an auto-resizing contenteditable div.
 * Hosts the slash-command menu and the mention picker.
 *
 * Supported types: paragraph, heading_1/2/3, bulleted_list_item,
 * numbered_list_item, to_do, toggle, quote, callout.
 *
 * Text content is stored in block.content.text (plain string with @<uuid>|
 * tokens for inline mentions). Changes are debounce-saved at 500 ms and
 * flushed immediately on blur or before any structural operation.
 *
 * Mention model
 * -------------
 * Mentions are rendered as <span contenteditable="false" data-mention-id="uuid">
 * chip elements. They are atomic: Backspace immediately before a chip removes the
 * whole chip rather than one character. The serialiser walks childNodes to produce
 * the @<uuid>| storage token from each chip.
 *
 * Icon rendering
 * --------------
 * Chips include the referenced block's icon via Iconify's synchronous getIcon()
 * cache. The icon is guaranteed to be cached because the MentionMenu renders it
 * via <Icon> before the user confirms a selection.
 */
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { Icon, getIcon, loadIcon } from '@iconify/vue'
import { useBlockStore, type Block } from '@/stores/blocks'
import { useSlashMenu } from '@/composables/useSlashMenu'
import { useMentionMenu } from '@/composables/useMentionMenu'
import { resolveColor } from '@/composables/blockColors'
import { useI18n } from 'vue-i18n'
import SlashMenu from './SlashMenu.vue'
import MentionMenu from './MentionMenu.vue'
import IconPicker from '@/components/IconPicker.vue'
import LinkedDatabasePicker from './LinkedDatabasePicker.vue'
import { WORKSPACE_ROOT_ID } from '@/constants'

// ── Toggle heading helpers ────────────────────────────────────────────────────

/** All four collapsible heading variants. */
const TOGGLE_HEADING_TYPES = new Set([
  'heading_1_toggle',
  'heading_2_toggle',
  'heading_3_toggle',
  'heading_4_toggle',
])

function isToggleHeadingType(type: string): boolean {
  return TOGGLE_HEADING_TYPES.has(type)
}

/**
 * Map a block type to the CSS class suffix used for editor styling.
 * Toggle heading types are styled identically to their static counterparts,
 * so "heading_1_toggle" resolves to "heading_1" for the class name.
 */
function editorTypeClass(type: string): string {
  if (isToggleHeadingType(type)) return type.replace('_toggle', '')
  // text_toggle renders with paragraph-level font size, not the bold toggle style.
  if (type === 'text_toggle') return 'paragraph'
  return type
}

// ── Props & Emits ─────────────────────────────────────────────────────────────

const props = defineProps<{
  block: Block
  parentId: string
  /** Set to true by parent to request programmatic focus on this row. */
  focusRequested?: boolean
  /** 1-based position within a consecutive numbered list run. */
  listIndex?: number
  /** Current open/closed state forwarded from parent for toggle blocks. */
  toggleOpen?: boolean
}>()

const emit = defineEmits<{
  /**
   * Request the parent to create a new block of the given type after this
   * block. Defaults to 'paragraph' when no type is specified.
   */
  (e: 'create-after', blockId: string, type: string): void
  /** Request the parent to delete this block and move focus upward. */
  (e: 'delete-self', blockId: string): void
  /** Request focus to move to the adjacent block. */
  (e: 'navigate', blockId: string, direction: 'up' | 'down'): void
  /** Fired once after focusRequested was consumed. */
  (e: 'focus-consumed'): void
  /** Request the parent to toggle the open/closed state of a toggle block. */
  (e: 'toggle-open', blockId: string): void
  /**
   * Request the parent to create a linked_database block after this block,
   * pointing to the given database ID.
   */
  (e: 'create-linked-db', afterBlockId: string, targetDbId: string): void
}>()

// ── Dependencies ──────────────────────────────────────────────────────────────

const { t } = useI18n()
const router = useRouter()
const blockStore = useBlockStore()
const slashMenu = useSlashMenu()
const mentionMenu = useMentionMenu()

// ── Mention storage format ────────────────────────────────────────────────────
// Storage format: @<uuid>|   (saved in block.content.text)
// DOM format:     <span contenteditable="false" data-mention-id="<uuid>">
// Regex matches a UUID: 8-4-4-4-12 hex chars
const MENTION_STORAGE_RE = /@([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\|/g

/**
 * Anchor for the current open mention-menu session: the text node and the
 * offset within it where the triggering @ was typed. Null when no mention
 * session is active.
 */
let _mentionAnchor: { node: Text; offset: number } | null = null

// ── Focus state ───────────────────────────────────────────────────────────────

/** Guards against applying remote (WebSocket) updates while the user is typing. */
const isFocused = ref(false)

// ── Linked database picker ────────────────────────────────────────────────────

/** True while the user is choosing a target database for a linked_database block. */
const showLinkedDbPicker = ref(false)

// ── Placeholder visibility ────────────────────────────────────────────────────

/**
 * True when the editor contains no text and no chips.
 * Drives the data-empty attribute which activates the ::before placeholder.
 */
const editorEmpty = ref(false)

// ── Color (#26) ───────────────────────────────────────────────────────────────

const editorRowStyle = computed<Record<string, string>>(() => {
  const hex = resolveColor(props.block.content?.color as string | undefined)
  if (!hex) return {}
  return { '--block-text-color': hex } as Record<string, string>
})

// ── DOM ref & save timer ──────────────────────────────────────────────────────

const editorEl = ref<HTMLElement | null>(null)
let saveTimer: ReturnType<typeof setTimeout> | null = null

// ── Helpers ───────────────────────────────────────────────────────────────────

function readBlockText(block: Block): string {
  return (block.content?.text as string | undefined) ?? ''
}

function autoResize(el: HTMLElement): void {
  if (el.offsetParent === null) return
  el.style.height = 'auto'
  el.style.height = `${el.scrollHeight}px`
}

// ── Cursor utilities ──────────────────────────────────────────────────────────

function moveCursorToEnd(el: HTMLElement): void {
  const range = document.createRange()
  range.selectNodeContents(el)
  range.collapse(false)
  const sel = window.getSelection()
  if (sel) { sel.removeAllRanges(); sel.addRange(range) }
}

function isCaretAtStart(el: HTMLElement): boolean {
  const sel = window.getSelection()
  if (!sel?.rangeCount || !sel.getRangeAt(0).collapsed) return false
  const range = sel.getRangeAt(0)
  const startRange = document.createRange()
  startRange.setStart(el, 0)
  startRange.collapse(true)
  return range.compareBoundaryPoints(Range.START_TO_START, startRange) === 0
}

function isCaretAtEnd(el: HTMLElement): boolean {
  const sel = window.getSelection()
  if (!sel?.rangeCount || !sel.getRangeAt(0).collapsed) return false
  const range = sel.getRangeAt(0)
  const endRange = document.createRange()
  endRange.selectNodeContents(el)
  endRange.collapse(false)
  return range.compareBoundaryPoints(Range.END_TO_END, endRange) === 0
}

/** Insert plain text at the current cursor position. */
function insertTextAtCursor(text: string): void {
  const sel = window.getSelection()
  if (!sel?.rangeCount) return
  const range = sel.getRangeAt(0)
  range.deleteContents()
  const node = document.createTextNode(text)
  range.insertNode(node)
  range.setStartAfter(node)
  range.collapse(true)
  sel.removeAllRanges()
  sel.addRange(range)
  const el = editorEl.value
  if (el) autoResize(el)
}

// ── Mention chip construction ─────────────────────────────────────────────────

const DEFAULT_PAGE_ICON = 'mdi:file-document-outline'
const DEFAULT_DB_ICON   = 'mdi:table'

function resolveBlockIcon(block: Block | null): string {
  if (block?.icon && block.icon.includes(':')) return block.icon
  return block?.type === 'database' ? DEFAULT_DB_ICON : DEFAULT_PAGE_ICON
}

/**
 * Build an SVG element from Iconify's synchronous icon cache.
 * Returns null when the icon is not cached — the chip renders without an icon.
 */
function buildIconSvg(iconName: string, size = 14): SVGElement | null {
  try {
    const data = getIcon(iconName) as { body: string; width?: number; height?: number } | null
    if (!data) return null
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
    svg.setAttribute('width', String(size))
    svg.setAttribute('height', String(size))
    svg.setAttribute('viewBox', `0 0 ${data.width ?? 24} ${data.height ?? 24}`)
    svg.setAttribute('aria-hidden', 'true')
    svg.innerHTML = data.body
    return svg
  } catch {
    return null
  }
}

/**
 * Construct a mention chip DOM node.
 *
 * The element carries contentEditable="false" so the browser treats it as an
 * atomic unit — the caret cannot enter it and it is skipped as a whole by
 * arrow-key navigation.
 */
function buildChipElement(blockId: string, block: Block | null): HTMLElement {
  const chip = document.createElement('span')
  chip.className = 'editor-row__mention-chip'
  chip.contentEditable = 'false'
  chip.setAttribute('data-mention-id', blockId)

  const iconWrap = document.createElement('span')
  iconWrap.className = 'editor-row__mention-chip__icon'
  const iconName = resolveBlockIcon(block)
  const svgSync  = buildIconSvg(iconName)
  if (svgSync) {
    iconWrap.appendChild(svgSync)
  } else {
    // Icon not yet in Iconify's cache (common when loading existing content
    // before the MentionMenu has ever been rendered for this session).
    // Load it async and patch the container once the data arrives.
    loadIcon(iconName)
      .then(() => {
        const svg = buildIconSvg(iconName)
        if (svg && iconWrap.isConnected) {
          iconWrap.textContent = ''
          iconWrap.appendChild(svg)
        }
      })
      .catch(() => { /* icon unavailable — chip renders without icon */ })
  }
  chip.appendChild(iconWrap)

  const titleSpan = document.createElement('span')
  titleSpan.className = 'editor-row__mention-chip__title'
  titleSpan.textContent =
    (block?.content?.title as string | undefined) ?? blockId.slice(0, 8) + '…'
  chip.appendChild(titleSpan)

  chip.addEventListener('click', (e: MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    router.push(`/blocks/${blockId}`)
  })

  return chip
}

// ── Storage ↔ DOM conversion ──────────────────────────────────────────────────

/**
 * Serialise the contenteditable's current DOM to the storage format.
 * Text nodes are preserved as-is; chip nodes become @<uuid>|; <br> becomes \n.
 * Any other unexpected element falls back to its textContent.
 */
function serializeToStorage(): string {
  const el = editorEl.value
  if (!el) return ''
  let result = ''
  for (const child of el.childNodes) {
    if (child.nodeType === Node.TEXT_NODE) {
      result += child.textContent ?? ''
    } else if (child.nodeType === Node.ELEMENT_NODE) {
      const elem = child as HTMLElement
      const mentionId = elem.dataset.mentionId
      if (mentionId) {
        result += `@${mentionId}|`
      } else if (elem.tagName === 'BR') {
        result += '\n'
      } else {
        // Unexpected nested element — extract its text as a safe fallback.
        result += elem.textContent ?? ''
      }
    }
  }
  return result
}

/** Update the editorEmpty flag from the current DOM state. */
function syncEmptyState(): void {
  editorEmpty.value = serializeToStorage() === ''
}

/**
 * Synchronously rebuild the contenteditable DOM from a storage-format string.
 * Uses whatever block data is currently available in the store.
 *
 * Must only be called when the editor is not actively focused (the editor is
 * cleared and rebuilt, which would destroy the current cursor position).
 */
function applyContentToDOMSync(storageText: string): void {
  const el = editorEl.value
  if (!el) return

  // Clear all children.
  el.textContent = ''

  const re = new RegExp(MENTION_STORAGE_RE.source, 'g')
  let lastIdx = 0
  let m: RegExpExecArray | null

  while ((m = re.exec(storageText)) !== null) {
    if (m.index > lastIdx) {
      el.appendChild(document.createTextNode(storageText.slice(lastIdx, m.index)))
    }
    const blockId = m[1]
    el.appendChild(buildChipElement(blockId, blockStore.blocks[blockId] ?? null))
    lastIdx = m.index + m[0].length
  }

  if (lastIdx < storageText.length) {
    el.appendChild(document.createTextNode(storageText.slice(lastIdx)))
  }

  syncEmptyState()
}

/**
 * Load a storage-format string into the editor, fetching any unknown block IDs
 * in the background. A synchronous pass renders immediately; a second pass
 * re-renders with resolved titles once the fetches complete.
 */
async function loadContentFromStorage(storageText: string): Promise<void> {
  applyContentToDOMSync(storageText)

  const missingIds: string[] = []
  const re = new RegExp(MENTION_STORAGE_RE.source, 'g')
  let m: RegExpExecArray | null
  while ((m = re.exec(storageText)) !== null) {
    if (!blockStore.blocks[m[1]]) missingIds.push(m[1])
  }
  if (!missingIds.length) return

  await Promise.all(missingIds.map((id) => blockStore.fetchBlock(id).catch(() => {})))

  if (!isFocused.value) {
    applyContentToDOMSync(storageText)
    nextTick(() => { if (editorEl.value) autoResize(editorEl.value) })
  }
}

// ── Save helpers ──────────────────────────────────────────────────────────────

function scheduleSave(): void {
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(() => {
    saveTimer = null
    blockStore.updateBlock(props.block.id, {
      content: { ...(props.block.content ?? {}), text: serializeToStorage() },
    })
  }, 500)
}

async function flushSave(): Promise<void> {
  if (saveTimer) { clearTimeout(saveTimer); saveTimer = null }
  await blockStore.updateBlock(props.block.id, {
    content: { ...(props.block.content ?? {}), text: serializeToStorage() },
  })
}

onBeforeUnmount(() => {
  if (saveTimer) { clearTimeout(saveTimer); saveTimer = null }
})

// ── Initialisation ────────────────────────────────────────────────────────────

onMounted(async () => {
  await loadContentFromStorage(readBlockText(props.block))
  if (editorEl.value) autoResize(editorEl.value)
})

// Sync external (WebSocket) updates only while this editor is not focused.
watch(
  () => props.block.content?.text,
  (newText) => {
    if (!isFocused.value) {
      loadContentFromStorage((newText as string | undefined) ?? '').then(() => {
        nextTick(() => { if (editorEl.value) autoResize(editorEl.value) })
      })
    }
  },
)

// ── Focus management ──────────────────────────────────────────────────────────

watch(
  () => props.focusRequested,
  async (requested) => {
    if (!requested) return
    await nextTick()
    const el = editorEl.value
    if (!el) return
    isFocused.value = true
    el.focus()
    moveCursorToEnd(el)
    emit('focus-consumed')
  },
)

function onFocus(): void {
  isFocused.value = true
  nextTick(() => { if (editorEl.value) autoResize(editorEl.value) })
}

function onBlur(): void {
  isFocused.value = false
  flushSave()
}

// ── Computed styles / attributes ──────────────────────────────────────────────

const CONTINUATION_TYPES = new Set(['bulleted_list_item', 'numbered_list_item', 'to_do'])

const nextBlockType = computed<string>(() =>
  CONTINUATION_TYPES.has(props.block.type) ? props.block.type : 'paragraph',
)

const CONVERT_ON_EMPTY_TYPES = new Set([
  'bulleted_list_item',
  'numbered_list_item',
  'to_do',
  'toggle',
  'quote',
  'callout',
])

const editorClass = computed(() => [
  'editor-row__textarea',
  `editor-row__textarea--${editorTypeClass(props.block.type)}`,
  props.block.type === 'to_do' && props.block.content?.checked
    ? 'editor-row__textarea--checked'
    : '',
])

const placeholder = computed<string>(() => {
  // Toggle heading types share the same placeholder as their static counterpart.
  const baseType = isToggleHeadingType(props.block.type)
    ? props.block.type.replace('_toggle', '')
    : props.block.type
  switch (baseType) {
    case 'heading_1':
    case 'heading_2':
    case 'heading_3':
    case 'heading_4':
      return t(`block.types.${baseType}`)
    default:
      return t('editor.slashMenu.emptyPlaceholder')
  }
})

// ── Input handler ─────────────────────────────────────────────────────────────

// ── Markdown prefix triggers ──────────────────────────────────────────────────

/**
 * Block types eligible for inline markdown prefix conversion.
 * List items, toggles, quotes and callouts are intentionally excluded —
 * only plain text and heading variants participate.
 */
const MARKDOWN_TRIGGER_TYPES = new Set([
  'paragraph', 'text_toggle',
  'heading_1', 'heading_2', 'heading_3', 'heading_4',
  'heading_1_toggle', 'heading_2_toggle', 'heading_3_toggle', 'heading_4_toggle',
])

/**
 * Ordered longest-first so that longer prefixes (e.g. `>#### `) are checked
 * before their substrings (`># `, `> `). The sentinel value `'TOGGLE_VARIANT'`
 * means "derive the target type from the current block type".
 */
const MARKDOWN_TRIGGERS: Array<{ prefix: string; targetType: string }> = [
  { prefix: '>#### ', targetType: 'heading_4_toggle' },
  { prefix: '>### ',  targetType: 'heading_3_toggle' },
  { prefix: '>## ',   targetType: 'heading_2_toggle' },
  { prefix: '># ',    targetType: 'heading_1_toggle' },
  { prefix: '> ',     targetType: 'TOGGLE_VARIANT'   },
  { prefix: '#### ',  targetType: 'heading_4'        },
  { prefix: '### ',   targetType: 'heading_3'        },
  { prefix: '## ',    targetType: 'heading_2'        },
  { prefix: '# ',     targetType: 'heading_1'        },
]

/**
 * Returns the toggle variant of a block type, or null when the block is
 * already a toggle type (or has no defined toggle counterpart).
 */
function toggleVariantOf(type: string): string | null {
  if (type === 'text_toggle' || type === 'toggle' || isToggleHeadingType(type)) return null
  if (type === 'paragraph') return 'text_toggle'
  if (/^heading_[1-4]$/.test(type)) return type + '_toggle'
  return null
}

/**
 * Check whether the block's current text starts with a markdown trigger prefix.
 * If so, strip the prefix, convert the block type, update the DOM and cursor,
 * and return true. Returns false when no trigger matched.
 *
 * Called from onInput only when no slash/mention menu is open.
 */
async function checkMarkdownTrigger(): Promise<boolean> {
  const text = serializeToStorage()
  for (const { prefix, targetType } of MARKDOWN_TRIGGERS) {
    if (!text.startsWith(prefix)) continue

    const resolvedType =
      targetType === 'TOGGLE_VARIANT' ? toggleVariantOf(props.block.type) : targetType

    // No-op when the block is already the target type or has no toggle variant.
    if (!resolvedType || resolvedType === props.block.type) return false

    const remaining = text.slice(prefix.length)

    if (saveTimer) { clearTimeout(saveTimer); saveTimer = null }
    await blockStore.updateBlock(props.block.id, {
      type: resolvedType,
      content: { ...(props.block.content ?? {}), text: remaining },
    })

    await nextTick()
    const el = editorEl.value
    if (el) {
      applyContentToDOMSync(remaining)
      autoResize(el)
      syncEmptyState()
      // Cursor to position 0 (immediately after the stripped prefix).
      const r = document.createRange()
      const fc = el.firstChild
      fc ? r.setStart(fc, 0) : r.setStart(el, 0)
      r.collapse(true)
      const s = window.getSelection()
      if (s) { s.removeAllRanges(); s.addRange(r) }
    }
    return true
  }
  return false
}

async function onInput(_e: Event): Promise<void> {
  const el = editorEl.value
  if (!el) return

  autoResize(el)
  syncEmptyState()

  const sel = window.getSelection()
  if (!sel?.rangeCount) {
    scheduleSave()
    return
  }
  const range = sel.getRangeAt(0)

  // Extract text content of the current text node up to the cursor.
  // Scoping to the current text node prevents @ or / characters inside
  // already-inserted chip titles from re-triggering menu detection.
  let textBeforeCursor = ''
  let currentTextNode: Text | null = null
  if (range.startContainer.nodeType === Node.TEXT_NODE) {
    currentTextNode = range.startContainer as Text
    textBeforeCursor = (currentTextNode.textContent ?? '').slice(0, range.startOffset)
  }

  // ── Slash menu ──────────────────────────────────────────────────────────────
  const slashIdx = textBeforeCursor.lastIndexOf('/')
  if (slashIdx !== -1) {
    const query = textBeforeCursor.slice(slashIdx + 1)
    if (!slashMenu.isOpen.value) {
      mentionMenu.close()
      _mentionAnchor = null
      slashMenu.open(el.getBoundingClientRect())
    }
    slashMenu.updateQuery(query)
  } else if (slashMenu.isOpen.value) {
    slashMenu.close()
  }

  // ── Mention menu ────────────────────────────────────────────────────────────
  if (!slashMenu.isOpen.value) {
    const lastAt = textBeforeCursor.lastIndexOf('@')
    if (lastAt !== -1) {
      const query = textBeforeCursor.slice(lastAt + 1)
      const charBefore = lastAt > 0 ? textBeforeCursor[lastAt - 1] : ' '
      // Valid trigger: @ preceded by whitespace or at position 0, query has no |
      const validTrigger = (/\s/.test(charBefore) || lastAt === 0) && !query.includes('|')

      if (validTrigger && currentTextNode) {
        if (!mentionMenu.isOpen.value) {
          _mentionAnchor = { node: currentTextNode, offset: lastAt }
          const pages = Object.values(blockStore.blocks).filter(
            (b) => b.state === 'active' && (b.type === 'page' || b.type === 'database'),
          )
          if (!blockStore.hasLoadedChildren(WORKSPACE_ROOT_ID)) {
            blockStore.fetchChildren(WORKSPACE_ROOT_ID)
          }
          mentionMenu.open(el.getBoundingClientRect(), pages)
        }
        mentionMenu.updateQuery(query)
      } else if (mentionMenu.isOpen.value) {
        mentionMenu.close()
        _mentionAnchor = null
      }
    } else if (mentionMenu.isOpen.value) {
      mentionMenu.close()
      _mentionAnchor = null
    }
  }

  // ── Markdown prefix triggers ────────────────────────────────────────────────
  // Only when no overlay menu is active, to avoid interfering with / or @.
  if (!slashMenu.isOpen.value && !mentionMenu.isOpen.value && MARKDOWN_TRIGGER_TYPES.has(props.block.type)) {
    const triggered = await checkMarkdownTrigger()
    if (triggered) return  // Type conversion is the mutation — skip debounce save.
  }

  scheduleSave()
}

/**
 * Force plain-text paste. contenteditable would otherwise accept rich HTML from
 * the clipboard and insert unexpected DOM nodes.
 */
function onPaste(e: ClipboardEvent): void {
  e.preventDefault()
  const text = e.clipboardData?.getData('text/plain') ?? ''
  if (!text) return
  insertTextAtCursor(text)
  syncEmptyState()
  scheduleSave()
}

// ── Keyboard handler ──────────────────────────────────────────────────────────

async function onKeydown(e: KeyboardEvent): Promise<void> {
  // ── Linked database picker is open: only Escape dismisses it. ──────────────
  if (showLinkedDbPicker.value) {
    if (e.key === 'Escape') {
      e.preventDefault()
      showLinkedDbPicker.value = false
    }
    return
  }

  // ── Slash menu is open: intercept navigation / confirm / dismiss. ───────────
  if (slashMenu.isOpen.value) {
    switch (e.key) {
      case 'Escape':
        e.preventDefault()
        slashMenu.close()
        return
      case 'ArrowDown':
        e.preventDefault()
        slashMenu.navigate('down')
        return
      case 'ArrowUp':
        e.preventDefault()
        slashMenu.navigate('up')
        return
      case 'Enter':
      case 'Tab': {
        e.preventDefault()
        const item = slashMenu.getActiveItem()
        if (item) applySlashSelection(item.type)
        return
      }
      case 'Backspace':
        // Let backspace fall through; onInput will close the menu when / disappears.
        return
    }
    return
  }

  // ── Mention menu is open: intercept navigation / confirm / dismiss. ─────────
  if (mentionMenu.isOpen.value) {
    switch (e.key) {
      case 'Escape':
        e.preventDefault()
        mentionMenu.close()
        _mentionAnchor = null
        return
      case 'ArrowDown':
        e.preventDefault()
        mentionMenu.navigate('down')
        return
      case 'ArrowUp':
        e.preventDefault()
        mentionMenu.navigate('up')
        return
      case 'Enter':
      case 'Tab': {
        e.preventDefault()
        const page = mentionMenu.getActivePage()
        if (page) applyMentionSelection(page)
        return
      }
      case 'Backspace': {
        // If the caret retreats to/before the triggering @, close the menu.
        // Do NOT return early — let the key fall through so the character is deleted.
        if (_mentionAnchor) {
          const sel = window.getSelection()
          if (sel?.rangeCount) {
            const range = sel.getRangeAt(0)
            const atSameNode = range.startContainer === _mentionAnchor.node
            const beforeAt   = atSameNode && range.startOffset <= _mentionAnchor.offset + 1
            if (beforeAt || !atSameNode) {
              mentionMenu.close()
              _mentionAnchor = null
            }
          }
        }
        break // fall through to normal editing
      }
    }
    return
  }

  // ── Normal editing ──────────────────────────────────────────────────────────
  const el = editorEl.value

  switch (e.key) {
    case 'Enter': {
      e.preventDefault()
      if (!e.shiftKey) {
        flushSave()
        emit('create-after', props.block.id, nextBlockType.value)
      } else {
        // Shift+Enter: insert a literal newline character in the current block.
        insertTextAtCursor('\n')
        scheduleSave()
      }
      break
    }

    case 'Backspace': {
      if (!el) break

      // ── Position-0 conversions (fire regardless of content length) ──────────
      // These run before the empty-block check so they apply to non-empty blocks too.
      if (isCaretAtStart(el)) {
        // Toggle heading or text_toggle at position 0 → static variant, cursor stays at 0.
        if (isToggleHeadingType(props.block.type) || props.block.type === 'text_toggle') {
          e.preventDefault()
          const newType = props.block.type === 'text_toggle'
            ? 'paragraph'
            : props.block.type.replace('_toggle', '')
          const currentText = serializeToStorage()
          await blockStore.updateBlock(props.block.id, {
            type: newType,
            content: { ...(props.block.content ?? {}), text: currentText },
          })
          await nextTick()
          if (editorEl.value) {
            autoResize(editorEl.value)
            const r = document.createRange()
            const fc = editorEl.value.firstChild
            fc ? r.setStart(fc, 0) : r.setStart(editorEl.value, 0)
            r.collapse(true)
            const s = window.getSelection()
            if (s) { s.removeAllRanges(); s.addRange(r) }
          }
          break
        }
        // Static heading at position 0 → paragraph, cursor stays at 0.
        if (/^heading_[1-4]$/.test(props.block.type)) {
          e.preventDefault()
          const currentText = serializeToStorage()
          await blockStore.updateBlock(props.block.id, {
            type: 'paragraph',
            content: { ...(props.block.content ?? {}), text: currentText },
          })
          await nextTick()
          if (editorEl.value) {
            autoResize(editorEl.value)
            const r = document.createRange()
            const fc = editorEl.value.firstChild
            fc ? r.setStart(fc, 0) : r.setStart(editorEl.value, 0)
            r.collapse(true)
            const s = window.getSelection()
            if (s) { s.removeAllRanges(); s.addRange(r) }
          }
          break
        }
      }

      // ── Empty block: convert type or request deletion from parent ────────────
      if (serializeToStorage() === '') {
        e.preventDefault()
        if (CONVERT_ON_EMPTY_TYPES.has(props.block.type)) {
          blockStore.updateBlock(props.block.id, { type: 'paragraph', content: { text: '' } })
        } else {
          emit('delete-self', props.block.id)
        }
        break
      }

      // Atomic chip deletion: if the caret is directly after a chip, remove it whole.
      const sel = window.getSelection()
      if (sel?.rangeCount && sel.getRangeAt(0).collapsed) {
        const range = sel.getRangeAt(0)
        const { startContainer, startOffset } = range
        let chipCandidate: Node | null = null

        if (startOffset === 0 && startContainer !== el) {
          // Caret at the very start of a child node — check its left sibling.
          chipCandidate = startContainer.previousSibling
        } else if (startContainer === el && startOffset > 0) {
          // Caret directly inside the editor div (between block-level children).
          chipCandidate = el.childNodes[startOffset - 1]
        }

        if (chipCandidate && (chipCandidate as HTMLElement).dataset?.mentionId) {
          e.preventDefault()
          chipCandidate.parentNode!.removeChild(chipCandidate)
          syncEmptyState()
          scheduleSave()
        }
      }
      break
    }

    case 'ArrowUp': {
      if (el && isCaretAtStart(el)) {
        e.preventDefault()
        emit('navigate', props.block.id, 'up')
      }
      break
    }

    case 'ArrowDown': {
      if (el && isCaretAtEnd(el)) {
        e.preventDefault()
        emit('navigate', props.block.id, 'down')
      }
      break
    }
  }
}

// ── To-do toggle ──────────────────────────────────────────────────────────────

function onToggleChecked(): void {
  blockStore.updateBlock(props.block.id, {
    content: {
      ...(props.block.content ?? {}),
      checked: !(props.block.content?.checked ?? false),
    },
  })
}

// ── Callout icon ──────────────────────────────────────────────────────────────

const DEFAULT_CALLOUT_ICON = 'mdi:lightbulb-outline'

const showCalloutIconPicker = ref(false)
const calloutIconPickerRect = ref<DOMRect | null>(null)

function onCalloutIconBtnClick(e: MouseEvent): void {
  calloutIconPickerRect.value = (e.currentTarget as HTMLElement).getBoundingClientRect()
  showCalloutIconPicker.value = !showCalloutIconPicker.value
}

function closeCalloutIconPicker(): void {
  showCalloutIconPicker.value = false
  calloutIconPickerRect.value = null
}

const calloutIcon = computed<string>(() => {
  const stored = props.block.content?.icon as string | undefined
  if (stored && stored.includes(':')) return stored
  return DEFAULT_CALLOUT_ICON
})

async function onCalloutIconUpdate(newIcon: string | null): Promise<void> {
  showCalloutIconPicker.value = false
  await blockStore.updateBlock(props.block.id, {
    content: {
      ...(props.block.content ?? {}),
      icon: newIcon ?? DEFAULT_CALLOUT_ICON,
    },
  })
}

// ── Mention selection ─────────────────────────────────────────────────────────

function applyMentionSelection(page: Block): void {
  mentionMenu.close()

  const el = editorEl.value
  if (!el) return

  const anchor = _mentionAnchor
  _mentionAnchor = null

  if (!anchor || !el.contains(anchor.node)) return

  const { node: atNode, offset: atOffset } = anchor

  // Determine the end of the @query: current caret position inside this node.
  const sel = window.getSelection()
  const cursorOffset =
    sel?.rangeCount && sel.getRangeAt(0).startContainer === atNode
      ? sel.getRangeAt(0).startOffset
      : atNode.textContent!.length

  const textBefore = atNode.textContent!.slice(0, atOffset)   // text before @
  const textAfter  = atNode.textContent!.slice(cursorOffset)  // text after @query

  const chip       = buildChipElement(page.id, page)
  const beforeNode = document.createTextNode(textBefore)
  const afterNode  = document.createTextNode(textAfter)

  const parent = atNode.parentNode!
  parent.insertBefore(beforeNode, atNode)
  parent.insertBefore(chip, atNode)
  parent.insertBefore(afterNode, atNode)
  parent.removeChild(atNode)

  // Place caret at the start of afterNode (immediately after the chip).
  nextTick(() => {
    const range = document.createRange()
    range.setStart(afterNode, 0)
    range.collapse(true)
    const s = window.getSelection()
    if (s) { s.removeAllRanges(); s.addRange(range) }
    if (el) autoResize(el)
  })

  syncEmptyState()
  scheduleSave()
}

// ── Slash menu selection ──────────────────────────────────────────────────────

async function applySlashSelection(type: string): Promise<void> {
  slashMenu.close()

  // Remove the / and any query text typed after it from the current text node.
  const sel = window.getSelection()
  if (sel?.rangeCount) {
    const range = sel.getRangeAt(0)
    if (range.startContainer.nodeType === Node.TEXT_NODE) {
      const textNode  = range.startContainer as Text
      const full      = textNode.textContent ?? ''
      const slashIdx  = full.lastIndexOf('/')
      if (slashIdx !== -1) {
        textNode.textContent = full.slice(0, slashIdx) + full.slice(range.startOffset)
        const newRange = document.createRange()
        newRange.setStart(textNode, slashIdx)
        newRange.collapse(true)
        sel.removeAllRanges()
        sel.addRange(newRange)
      }
    }
  }

  const el = editorEl.value
  if (el) autoResize(el)

  if (type === 'page') {
    await flushSave()
    emit('create-after', props.block.id, 'page')
    return
  }

  if (type === 'linked_database') {
    await flushSave()
    showLinkedDbPicker.value = true
    return
  }

  if (type === 'divider') {
    await blockStore.updateBlock(props.block.id, {
      type: 'divider',
      content: {},
    })
    emit('create-after', props.block.id, 'paragraph')
    return
  }

  // Table of contents: convert current block and create a fresh paragraph after
  // so the editor focus moves forward naturally.
  if (type === 'table_of_contents') {
    await blockStore.updateBlock(props.block.id, {
      type: 'table_of_contents',
      content: {},
    })
    emit('create-after', props.block.id, 'paragraph')
    return
  }

  const cleanText = serializeToStorage()
  await blockStore.updateBlock(props.block.id, {
    type,
    content: { ...(props.block.content ?? {}), text: cleanText },
  })
}
</script>

<template>
  <div class="editor-row" :style="editorRowStyle">
    <!-- Bullet prefix -->
    <span
      v-if="block.type === 'bulleted_list_item'"
      class="editor-row__list-prefix editor-row__bullet"
      aria-hidden="true"
    >•</span>

    <!-- Number prefix -->
    <span
      v-else-if="block.type === 'numbered_list_item'"
      class="editor-row__list-prefix editor-row__number"
      aria-hidden="true"
    >{{ listIndex }}.</span>

    <!-- Checkbox -->
    <button
      v-else-if="block.type === 'to_do'"
      class="editor-row__checkbox"
      :aria-label="block.content?.checked ? 'Uncheck' : 'Check'"
      tabindex="-1"
      @mousedown.prevent="onToggleChecked"
    >
      <Icon
        :icon="block.content?.checked ? 'mdi:checkbox-marked' : 'mdi:checkbox-blank-outline'"
        width="18"
        height="18"
      />
    </button>

    <!-- Toggle chevron — shown for regular toggle, text_toggle, and all toggle heading types -->
    <button
      v-else-if="block.type === 'toggle' || block.type === 'text_toggle' || isToggleHeadingType(block.type)"
      class="editor-row__toggle-chevron"
      aria-label="Toggle"
      tabindex="-1"
      @mousedown.prevent="emit('toggle-open', block.id)"
    >
      <Icon
        :icon="toggleOpen ? 'mdi:chevron-down' : 'mdi:chevron-right'"
        width="18"
        height="18"
      />
    </button>

    <!-- Callout icon -->
    <div
      v-else-if="block.type === 'callout'"
      class="editor-row__callout-icon-wrap"
    >
      <button
        class="editor-row__callout-icon-btn"
        title="Change icon"
        tabindex="-1"
        @click.stop="onCalloutIconBtnClick"
      >
        <Icon :icon="calloutIcon" width="18" height="18" />
      </button>
      <IconPicker
        v-if="showCalloutIconPicker"
        :model-value="calloutIcon"
        :trigger-rect="calloutIconPickerRect"
        @update:model-value="onCalloutIconUpdate"
        @close="closeCalloutIconPicker"
      />
    </div>

    <!--
      Contenteditable editor.
      Mention chips (<span contenteditable="false" data-mention-id="…">) live
      directly inside this div alongside regular text nodes. The block-type
      variant classes (editor-row__textarea--paragraph etc.) continue to apply
      font-size and weight rules defined in the style block below.
    -->
    <div
      ref="editorEl"
      :class="editorClass"
      :data-placeholder="placeholder"
      :data-empty="editorEmpty || undefined"
      class="editor-row__editor"
      contenteditable="true"
      spellcheck="true"
      @input="onInput"
      @keydown="onKeydown"
      @focus="onFocus"
      @blur="onBlur"
      @paste="onPaste"
    />

    <SlashMenu
      :show="slashMenu.isOpen.value"
      :items="slashMenu.filteredItems.value"
      :active-index="slashMenu.activeIndex.value"
      :anchor-rect="slashMenu.anchorRect.value"
      @select="applySlashSelection"
      @set-active="slashMenu.setActiveIndex"
      @close="slashMenu.close()"
    />

    <MentionMenu
      :show="mentionMenu.isOpen.value"
      :pages="mentionMenu.filteredPages.value"
      :active-index="mentionMenu.activeIndex.value"
      :anchor-rect="mentionMenu.anchorRect.value"
      @select="applyMentionSelection"
      @set-active="mentionMenu.setActiveIndex"
      @close="mentionMenu.close()"
    />

    <LinkedDatabasePicker
      v-if="showLinkedDbPicker"
      :anchor-rect="editorEl?.getBoundingClientRect() ?? null"
      @select="(dbId) => { showLinkedDbPicker = false; emit('create-linked-db', block.id, dbId) }"
      @close="showLinkedDbPicker = false"
    />
  </div>
</template>

<style scoped>
.editor-row {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: flex-start;
  gap: 6px;
}

/* ── Base editor (contenteditable div) ────────────────────────────────────── */
.editor-row__editor {
  flex: 1;
  min-width: 0;
  background: transparent;
  border: none;
  outline: none;
  font-family: inherit;
  color: var(--block-text-color, var(--color-text));
  padding: 2px 0;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  cursor: text;
  overflow: hidden;
  /* Height is managed by autoResize() via inline style.height. */
}

/* Placeholder shown when the editor has no content. */
.editor-row__editor[data-empty]::before {
  content: attr(data-placeholder);
  color: var(--color-text-muted);
  pointer-events: none;
  /* Position is inline so it flows with the text baseline. */
}

/* Chip styles live in the unscoped block below — see comment there. */

/* ── Block-type font variants ─────────────────────────────────────────────── */
.editor-row__textarea--paragraph {
  font-size: 0.9375rem;
}

.editor-row__textarea--heading_1 {
  font-size: 1.875rem;
  font-weight: 700;
  letter-spacing: -0.02em;
  line-height: 1.25;
}

.editor-row__textarea--heading_2 {
  font-size: 1.375rem;
  font-weight: 600;
  letter-spacing: -0.01em;
  line-height: 1.3;
}

.editor-row__textarea--heading_3 {
  font-size: 1.125rem;
  font-weight: 600;
  line-height: 1.35;
}

.editor-row__textarea--heading_4 {
  font-size: 1rem;
  font-weight: 600;
  line-height: 1.4;
}

.editor-row__textarea--bulleted_list_item,
.editor-row__textarea--numbered_list_item {
  font-size: 0.9375rem;
}

.editor-row__textarea--to_do {
  font-size: 0.9375rem;
}

.editor-row__textarea--checked {
  text-decoration: line-through;
  color: var(--color-text-muted);
}

.editor-row__textarea--toggle {
  font-size: 0.9375rem;
  font-weight: 500;
}

.editor-row__textarea--quote {
  font-size: 0.9375rem;
  font-style: italic;
  color: var(--color-text-muted);
}

.editor-row__textarea--callout {
  font-size: 0.9375rem;
}

/* ── List prefix (bullet / number) ───────────────────────────────────────── */
.editor-row__list-prefix {
  flex-shrink: 0;
  color: var(--color-text-muted);
  font-size: 0.9375rem;
  line-height: 1.6;
  padding: 2px 0;
  user-select: none;
}

.editor-row__number {
  min-width: 1.5rem;
  text-align: right;
}

/* ── Checkbox button ──────────────────────────────────────────────────────── */
.editor-row__checkbox {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: none;
  border: none;
  padding: 0;
  margin-top: 2px;
  cursor: pointer;
  color: var(--color-accent);
  line-height: 1;
}

.editor-row__checkbox:hover {
  opacity: 0.75;
}

/* ── Toggle chevron button ────────────────────────────────────────────────── */
.editor-row__toggle-chevron {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: none;
  border: none;
  padding: 0;
  margin-top: 2px;
  cursor: pointer;
  color: var(--color-text-muted);
  line-height: 1;
  transition: color 0.1s;
}

.editor-row__toggle-chevron:hover {
  color: var(--color-text);
}

/* ── Callout icon ─────────────────────────────────────────────────────────── */
.editor-row__callout-icon-wrap {
  position: relative;
  flex-shrink: 0;
  display: flex;
  align-items: flex-start;
  padding-top: 3px;
}

.editor-row__callout-icon-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  background: none;
  border: none;
  padding: 2px;
  border-radius: 4px;
  cursor: pointer;
  color: var(--color-text-muted);
  transition: background 0.1s, color 0.1s;
  line-height: 1;
}

.editor-row__callout-icon-btn:hover {
  background: var(--color-border);
  color: var(--color-text);
}

/* ── Print ────────────────────────────────────────────────────────────────── */
@media print {
  .editor-row__editor {
    overflow: visible !important;
    overflow-wrap: break-word !important;
    word-break: break-word !important;
    white-space: pre-wrap !important;
    max-height: none !important;
    border: none !important;
    background: transparent !important;
  }
}
</style>

<!--
  Chip styles are intentionally NOT scoped.
  Vue's scoped styles inject a data-v-* attribute onto elements created through
  the template, but mention chips are built via document.createElement() and
  inserted into the contenteditable at runtime. Those DOM nodes never receive
  the scoped attribute, so scoped selectors would never match them.
-->
<style>
.editor-row__mention-chip {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  color: var(--color-text);
  background: color-mix(in srgb, var(--color-text) 10%, transparent);
  border-radius: 3px;
  padding: 0 5px 0 4px;
  font-size: 0.9em;
  line-height: 1.4;
  vertical-align: middle;
  cursor: pointer;
  user-select: none;
  white-space: nowrap;
  transition: background 0.1s;
}

.editor-row__mention-chip:hover {
  background: color-mix(in srgb, var(--color-text) 18%, transparent);
}

.editor-row__mention-chip__icon {
  display: flex;
  align-items: center;
  flex-shrink: 0;
  opacity: 0.65;
}

.editor-row__mention-chip__title {
  /* Inherits font properties from the chip wrapper. */
}
</style>

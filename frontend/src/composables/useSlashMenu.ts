/**
 * useSlashMenu
 *
 * Per-component composable that manages slash-command menu state: open/close,
 * query string, active item index, anchor positioning, and keyboard navigation.
 *
 * Each BlockEditorRow creates its own instance; all reactive state is local
 * to the call-site.
 */
import { ref, computed } from 'vue'
import { useI18n } from 'vue-i18n'

export interface SlashMenuItem {
  type: string
  labelKey: string
  descKey: string
  icon: string
  /** Optional display-only group label shown above the first item of each group. */
  group?: string
}

/** Ordered list of items shown in the slash command menu. */
const ITEMS: SlashMenuItem[] = [
  // ── Text ──────────────────────────────────────────────────────────────────
  {
    type: 'paragraph',
    labelKey: 'block.types.paragraph',
    descKey: 'editor.slashMenu.paragraphDesc',
    icon: 'mdi:text',
    group: 'editor.slashMenu.groupText',
  },
  {
    type: 'heading_1',
    labelKey: 'block.types.heading_1',
    descKey: 'editor.slashMenu.h1Desc',
    icon: 'mdi:format-header-1',
  },
  {
    type: 'heading_2',
    labelKey: 'block.types.heading_2',
    descKey: 'editor.slashMenu.h2Desc',
    icon: 'mdi:format-header-2',
  },
  {
    type: 'heading_3',
    labelKey: 'block.types.heading_3',
    descKey: 'editor.slashMenu.h3Desc',
    icon: 'mdi:format-header-3',
  },
  {
    type: 'heading_4',
    labelKey: 'block.types.heading_4',
    descKey: 'editor.slashMenu.h4Desc',
    icon: 'mdi:format-header-4',
  },
  {
    type: 'bulleted_list_item',
    labelKey: 'block.types.bulleted_list_item',
    descKey: 'editor.slashMenu.bulletedListDesc',
    icon: 'mdi:format-list-bulleted',
  },
  {
    type: 'numbered_list_item',
    labelKey: 'block.types.numbered_list_item',
    descKey: 'editor.slashMenu.numberedListDesc',
    icon: 'mdi:format-list-numbered',
  },
  {
    type: 'to_do',
    labelKey: 'block.types.to_do',
    descKey: 'editor.slashMenu.toDoDesc',
    icon: 'mdi:checkbox-blank-outline',
  },
  {
    type: 'toggle',
    labelKey: 'block.types.toggle',
    descKey: 'editor.slashMenu.toggleDesc',
    icon: 'mdi:chevron-right-box-outline',
  },
  {
    type: 'text_toggle',
    labelKey: 'block.types.text_toggle',
    descKey: 'editor.slashMenu.textToggleDesc',
    icon: 'mdi:chevron-right-circle-outline',
  },
  {
    type: 'quote',
    labelKey: 'block.types.quote',
    descKey: 'editor.slashMenu.quoteDesc',
    icon: 'mdi:format-quote-open',
  },
  {
    type: 'callout',
    labelKey: 'block.types.callout',
    descKey: 'editor.slashMenu.calloutDesc',
    icon: 'mdi:lightbulb-outline',
  },
  {
    type: 'divider',
    labelKey: 'block.types.divider',
    descKey: 'editor.slashMenu.dividerDesc',
    icon: 'mdi:minus',
  },
  // ── Toggle Headings ────────────────────────────────────────────────────────
  {
    type: 'heading_1_toggle',
    labelKey: 'block.types.heading_1_toggle',
    descKey: 'editor.slashMenu.h1ToggleDesc',
    icon: 'mdi:chevron-right-box-outline',
    group: 'editor.slashMenu.groupToggleHeadings',
  },
  {
    type: 'heading_2_toggle',
    labelKey: 'block.types.heading_2_toggle',
    descKey: 'editor.slashMenu.h2ToggleDesc',
    icon: 'mdi:chevron-right-box-outline',
  },
  {
    type: 'heading_3_toggle',
    labelKey: 'block.types.heading_3_toggle',
    descKey: 'editor.slashMenu.h3ToggleDesc',
    icon: 'mdi:chevron-right-box-outline',
  },
  {
    type: 'heading_4_toggle',
    labelKey: 'block.types.heading_4_toggle',
    descKey: 'editor.slashMenu.h4ToggleDesc',
    icon: 'mdi:chevron-right-box-outline',
  },
  // ── Media (Tier 2a) ────────────────────────────────────────────────────────
  {
    type: 'image',
    labelKey: 'block.types.image',
    descKey: 'editor.slashMenu.imageDesc',
    icon: 'mdi:image-outline',
    group: 'editor.slashMenu.groupMedia',
  },
  {
    type: 'video',
    labelKey: 'block.types.video',
    descKey: 'editor.slashMenu.videoDesc',
    icon: 'mdi:video-outline',
  },
  {
    type: 'audio',
    labelKey: 'block.types.audio',
    descKey: 'editor.slashMenu.audioDesc',
    icon: 'mdi:music-note-outline',
  },
  {
    type: 'pdf',
    labelKey: 'block.types.pdf',
    descKey: 'editor.slashMenu.pdfDesc',
    icon: 'mdi:file-pdf-box',
  },
  // ── Files (Tier 2b) ────────────────────────────────────────────────────────
  {
    type: 'file',
    labelKey: 'block.types.file',
    descKey: 'editor.slashMenu.fileDesc',
    icon: 'mdi:file-upload-outline',
    group: 'editor.slashMenu.groupFiles',
  },
  {
    type: 'drive',
    labelKey: 'block.types.drive',
    descKey: 'editor.slashMenu.driveDesc',
    icon: 'mdi:folder-outline',
  },
  // ── Meta (Tier 2c) ─────────────────────────────────────────────────────────
  {
    type: 'bookmark',
    labelKey: 'block.types.bookmark',
    descKey: 'editor.slashMenu.bookmarkDesc',
    icon: 'mdi:bookmark-outline',
    group: 'editor.slashMenu.groupMeta',
  },
  {
    type: 'embed',
    labelKey: 'block.types.embed',
    descKey: 'editor.slashMenu.embedDesc',
    icon: 'mdi:code-tags',
  },
  // ── Structural (Tier 3 layout first, then synched, then page) ────────────────
  {
    type: 'layout',
    labelKey: 'block.types.layout',
    descKey: 'editor.slashMenu.layoutDesc',
    icon: 'mdi:view-column-outline',
    group: 'editor.slashMenu.groupStructure',
  },
  {
    type: 'synched_origin',
    labelKey: 'block.types.synched_origin',
    descKey: 'editor.slashMenu.synchedOriginDesc',
    icon: 'mdi:sync',
  },
  {
    type: 'table_of_contents',
    labelKey: 'block.types.table_of_contents',
    descKey: 'editor.slashMenu.tableOfContentsDesc',
    icon: 'mdi:format-list-bulleted',
  },
  {
    type: 'page',
    labelKey: 'block.types.page',
    descKey: 'editor.slashMenu.pageDesc',
    icon: 'mdi:file-outline',
  },
  // ── Database (Tier 1) ──────────────────────────────────────────────────────
  {
    type: 'database',
    labelKey: 'block.types.database',
    descKey: 'editor.slashMenu.databaseDesc',
    icon: 'mdi:table-large',
    group: 'editor.slashMenu.groupDatabase',
  },
  {
    type: 'linked_database',
    labelKey: 'block.types.linked_database',
    descKey: 'editor.slashMenu.linkedDatabaseDesc',
    icon: 'mdi:table-arrow-right',
  },
]

export function useSlashMenu() {
  const { t } = useI18n()

  const isOpen = ref(false)
  const query = ref('')
  const activeIndex = ref(0)
  const anchorRect = ref<DOMRect | null>(null)

  const filteredItems = computed<SlashMenuItem[]>(() => {
    if (!query.value) return ITEMS
    const q = query.value.toLowerCase()
    return ITEMS.filter(
      (item) =>
        t(item.labelKey).toLowerCase().includes(q) ||
        item.type.toLowerCase().includes(q) ||
        t(item.descKey).toLowerCase().includes(q),
    )
  })

  function open(rect: DOMRect): void {
    anchorRect.value = rect
    activeIndex.value = 0
    query.value = ''
    isOpen.value = true
  }

  function close(): void {
    isOpen.value = false
    query.value = ''
    activeIndex.value = 0
    anchorRect.value = null
  }

  function updateQuery(q: string): void {
    query.value = q
    // Clamp active index in case filtered list got shorter.
    const max = Math.max(0, filteredItems.value.length - 1)
    activeIndex.value = Math.min(activeIndex.value, max)
  }

  function navigate(direction: 'up' | 'down'): void {
    const count = filteredItems.value.length
    if (!count) return
    if (direction === 'down') {
      activeIndex.value = (activeIndex.value + 1) % count
    } else {
      activeIndex.value = (activeIndex.value - 1 + count) % count
    }
  }

  function setActiveIndex(idx: number): void {
    activeIndex.value = idx
  }

  function getActiveItem(): SlashMenuItem | null {
    return filteredItems.value[activeIndex.value] ?? null
  }

  return {
    isOpen,
    query,
    activeIndex,
    anchorRect,
    filteredItems,
    open,
    close,
    updateQuery,
    navigate,
    setActiveIndex,
    getActiveItem,
  }
}

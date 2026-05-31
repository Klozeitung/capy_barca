<script setup lang="ts">
/**
 * TableOfContentsBlock
 *
 * Renders a live heading outline for the current page, including headings
 * nested inside collapsed toggle blocks at any depth.
 *
 * On mount the component pre-fetches children of all toggle blocks on the
 * page so that nested headings are discoverable even before the user has
 * manually opened any toggle. The headings computed is reactive: it updates
 * automatically whenever the block store changes.
 *
 * Click navigation:
 * - If all ancestor toggles are already open, scrolls directly to the heading.
 * - If any ancestor toggle is collapsed, all are opened programmatically, the
 *   component waits for Vue to re-render (with a spinner shown in the meantime),
 *   then scrolls.
 *
 * Toggle heading state is irrelevant here — all headings always appear in the
 * TOC regardless of fold state. This also ensures correct export / print output.
 */
import { ref, computed, watch, onMounted, nextTick } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { useBlockStore, type Block } from '@/stores/blocks'
import { useToggleState } from '@/composables/useToggleState'

// ── Props ─────────────────────────────────────────────────────────────────────

const props = defineProps<{
  block: Block
  parentId: string
}>()

// ── Dependencies ──────────────────────────────────────────────────────────────

const { t } = useI18n()
const blockStore = useBlockStore()
const { toggleOpenStates } = useToggleState()

// ── Type sets ─────────────────────────────────────────────────────────────────

const ALL_HEADING_TYPES = new Set([
  'heading_1', 'heading_2', 'heading_3', 'heading_4',
  'heading_1_toggle', 'heading_2_toggle', 'heading_3_toggle', 'heading_4_toggle',
])

const ALL_TOGGLE_TYPES = new Set([
  'toggle', 'text_toggle',
  'heading_1_toggle', 'heading_2_toggle', 'heading_3_toggle', 'heading_4_toggle',
])

/**
 * Structural container blocks that are always visible and hold child blocks
 * without requiring user interaction to reveal them. The TOC recurses into
 * these but does not add them to ancestorToggleIds (no unfolding needed).
 */
const TRANSPARENT_CONTAINER_TYPES = new Set(['layout', 'column'])

// ── Heading type helpers ──────────────────────────────────────────────────────

function headingLevel(type: string): number {
  if (type.startsWith('heading_1')) return 1
  if (type.startsWith('heading_2')) return 2
  if (type.startsWith('heading_3')) return 3
  if (type.startsWith('heading_4')) return 4
  return 1
}

// ── Page resolution ───────────────────────────────────────────────────────────

/**
 * Walk up the block tree from the direct parent to find the nearest page
 * ancestor. Falls back to parentId when no page ancestor is found.
 */
const sourcePageId = computed<string>(() => {
  if (blockStore.blocks[props.parentId]?.type === 'page') return props.parentId
  let id: string | null = blockStore.blocks[props.parentId]?.parent_id ?? null
  while (id) {
    const b = blockStore.blocks[id]
    if (!b) break
    if (b.type === 'page') return id
    id = b.parent_id ?? null
  }
  return props.parentId
})

// ── Deep fetch ────────────────────────────────────────────────────────────────

/**
 * Recursively fetch children of all toggle blocks reachable from parentId so
 * that the headings computed can discover nested headings even inside collapsed
 * toggles. Runs concurrently across siblings, depth-limited to 8 levels.
 */
async function deepFetchChildren(parentId: string, depth = 0): Promise<void> {
  if (depth > 8) return
  if (!blockStore.hasLoadedChildren(parentId)) {
    await blockStore.fetchChildren(parentId)
  }
  await Promise.all(
    blockStore
      .getChildren(parentId)
      .filter((b) => b.state === 'active' && (ALL_TOGGLE_TYPES.has(b.type) || TRANSPARENT_CONTAINER_TYPES.has(b.type)))
      .map((b) => deepFetchChildren(b.id, depth + 1)),
  )
}

onMounted(() => { deepFetchChildren(sourcePageId.value) })
watch(sourcePageId, (id) => { deepFetchChildren(id) })

// ── Heading list ──────────────────────────────────────────────────────────────

interface HeadingEntry {
  id: string
  level: number
  text: string
  /** Ordered ancestor toggle block IDs (outermost first) required to reach this heading. */
  ancestorToggleIds: string[]
}

/**
 * Matches the inline mention storage token: @<uuid>|
 * The UUID is captured in group 1 so it can be resolved to a block title.
 * Format matches BlockEditorRow's serializeToStorage output exactly.
 */
const MENTION_STORAGE_RE = /@([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\|/g

/**
 * Resolve a block ID to its display title.
 * Pages and databases store their title in content.title; other block types
 * use content.text. Returns an empty string when the block is not loaded.
 */
function resolveBlockTitle(blockId: string): string {
  const block = blockStore.blocks[blockId]
  if (!block) return ''
  return (block.content?.title as string | undefined)
    ?? (block.content?.text as string | undefined)
    ?? ''
}

/**
 * Convert a raw storage string into plain display text by replacing every
 * @<uuid>| mention token with the referenced block's resolved title.
 */
function resolveStorageText(raw: string): string {
  return raw.replace(MENTION_STORAGE_RE, (_match, uuid: string) => resolveBlockTitle(uuid))
}

/**
 * Recursively collect heading entries from parentId downward.
 * Toggle blocks are traversed regardless of their open/closed state so that
 * collapsed sections still contribute headings to the TOC.
 */
function collectHeadings(parentId: string, ancestors: string[] = []): HeadingEntry[] {
  const result: HeadingEntry[] = []
  for (const block of blockStore.getChildren(parentId)) {
    if (block.state !== 'active') continue
    if (ALL_HEADING_TYPES.has(block.type)) {
      result.push({
        id: block.id,
        level: headingLevel(block.type),
        text: resolveStorageText((block.content?.text as string | undefined) ?? '').trim(),
        ancestorToggleIds: [...ancestors],
      })
    }
    if (ALL_TOGGLE_TYPES.has(block.type)) {
      // Toggle children: add to ancestors so the TOC can open them if collapsed.
      result.push(...collectHeadings(block.id, [...ancestors, block.id]))
    } else if (TRANSPARENT_CONTAINER_TYPES.has(block.type)) {
      // Layout/column: always visible, no unfolding needed — ancestors unchanged.
      result.push(...collectHeadings(block.id, [...ancestors]))
    }
  }
  return result
}

const headings = computed<HeadingEntry[]>(() => collectHeadings(sourcePageId.value))

// ── Navigation ────────────────────────────────────────────────────────────────

/** True while ancestor toggles are being opened and the DOM is re-rendering. */
const isNavigating = ref(false)

async function scrollToHeading(heading: HeadingEntry): Promise<void> {
  const collapsedAncestors = heading.ancestorToggleIds.filter(
    (id) => !(toggleOpenStates.value[id] ?? false),
  )

  if (collapsedAncestors.length === 0) {
    // All ancestors already open — scroll immediately.
    document
      .querySelector(`[data-block-id="${heading.id}"]`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    return
  }

  isNavigating.value = true
  try {
    // Open all collapsed ancestors in one pass.
    for (const id of heading.ancestorToggleIds) {
      toggleOpenStates.value[id] = true
    }

    // Fetch children for any toggles that haven't been loaded yet.
    await Promise.all(
      collapsedAncestors
        .filter((id) => !blockStore.hasLoadedChildren(id))
        .map((id) => blockStore.fetchChildren(id)),
    )

    // Wait for Vue to process the reactive updates and paint the new blocks.
    await nextTick()
    await nextTick()
    await new Promise<void>((r) => setTimeout(r, 130))

    document
      .querySelector(`[data-block-id="${heading.id}"]`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  } finally {
    isNavigating.value = false
  }
}

// ── Indentation ───────────────────────────────────────────────────────────────

function indentStyle(heading: HeadingEntry): Record<string, string> {
  // Extra indent for headings nested inside toggle blocks so their depth is
  // visually represented in addition to their heading level.
  const nestDepth = heading.ancestorToggleIds.length
  return { paddingLeft: `${(heading.level - 1) * 14 + nestDepth * 8}px` }
}
</script>

<template>
  <div class="toc-block">
    <div class="toc-block__header">
      <Icon icon="mdi:format-list-bulleted" width="13" height="13" class="toc-block__header-icon" />
      <span class="toc-block__header-label">{{ t('tableOfContents.title') }}</span>
      <span v-if="isNavigating" class="toc-block__spinner" aria-label="Loading" />
    </div>

    <div v-if="headings.length === 0" class="toc-block__empty">
      {{ t('tableOfContents.empty') }}
    </div>

    <nav v-else class="toc-block__nav" aria-label="Table of contents">
      <button
        v-for="heading in headings"
        :key="heading.id"
        class="toc-block__item"
        :class="[
          `toc-block__item--level-${heading.level}`,
          { 'toc-block__item--nested': heading.ancestorToggleIds.length > 0 },
        ]"
        :style="indentStyle(heading)"
        :disabled="isNavigating"
        @click="scrollToHeading(heading)"
      >
        <Icon
          v-if="heading.ancestorToggleIds.length > 0"
          icon="mdi:subdirectory-arrow-right"
          width="11"
          height="11"
          class="toc-block__item-nest-icon"
        />
        {{ heading.text || t('main.untitled') }}
      </button>
    </nav>
  </div>
</template>

<style scoped>
.toc-block {
  width: 100%;
  background: var(--color-hover);
  border: 1px solid var(--color-border);
  border-radius: 6px;
  padding: 10px 14px 12px;
  box-sizing: border-box;
}

.toc-block__header {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-bottom: 8px;
}

.toc-block__header-icon {
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.toc-block__header-label {
  font-size: 0.7rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.07em;
  color: var(--color-text-muted);
  user-select: none;
  flex: 1;
}

/* Animated loading indicator shown while ancestor toggles are being opened */
.toc-block__spinner {
  display: inline-block;
  width: 12px;
  height: 12px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-accent);
  border-radius: 50%;
  animation: toc-spin 0.6s linear infinite;
  flex-shrink: 0;
}

@keyframes toc-spin {
  to { transform: rotate(360deg); }
}

.toc-block__empty {
  font-size: 0.8125rem;
  color: var(--color-text-muted);
  font-style: italic;
}

.toc-block__nav {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.toc-block__item {
  display: flex;
  align-items: center;
  gap: 3px;
  width: 100%;
  background: none;
  border: none;
  padding: 2px 6px;
  border-radius: 4px;
  text-align: left;
  cursor: pointer;
  color: var(--color-accent);
  font-family: inherit;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  transition: background 0.1s;
  box-sizing: border-box;
}

.toc-block__item:hover:not(:disabled) {
  background: var(--color-active);
  text-decoration: underline;
}

.toc-block__item:disabled {
  cursor: wait;
  opacity: 0.6;
}

.toc-block__item-nest-icon {
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.toc-block__item--level-1 {
  font-size: 0.875rem;
  font-weight: 600;
}

.toc-block__item--level-2 {
  font-size: 0.8375rem;
  font-weight: 500;
}

.toc-block__item--level-3 {
  font-size: 0.8125rem;
  font-weight: 400;
}

.toc-block__item--level-4 {
  font-size: 0.7875rem;
  font-weight: 400;
  color: var(--color-text-muted);
}

/* ── Print ───────────────────────────────────────────────────────────────── */
@media print {
  .toc-block {
    border: none;
    background: none;
    padding: 0;
  }

  .toc-block__item {
    color: inherit;
    text-decoration: none;
  }

  .toc-block__spinner {
    display: none;
  }
}
</style>

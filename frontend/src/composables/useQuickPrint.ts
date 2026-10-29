/**
 * useQuickPrint
 *
 * Quick Print sends a page, or a database entry, as plain text to the custom
 * printer set up under Settings → API. Without a printer, or without the print
 * permission, the same text is downloaded as a .txt file instead.
 *
 * What is printed
 * ---------------
 * The title, the properties that have a value, and the text blocks of the
 * page in document order:
 *
 *     # <title>
 *
 *     ...... <property>
 *     <value>
 *
 *     <text block>
 *
 *     <text block>
 *
 * Property names are indented with a dotted leader, as old server logs did,
 * which needs no markup the receiving side would have to understand. Which
 * properties appear, and in what order, depends on where the print starts:
 * from a table row, the view's visible columns in the view's order; from the
 * page settings, the property section's groups, order and visibility.
 *
 * Text blocks are every block that carries text (paragraphs, headings, list
 * items, to-dos, toggles, quotes, callouts, code), including those nested in
 * toggles, columns and synced blocks. Inline mentions print as the title of
 * the mentioned page. A to-do starts with ``[ ]`` or ``[x]``; a checkbox
 * property prints the same marks, in both states. Nothing else is converted:
 * character sets, line widths and markup are the printer's business.
 *
 * Sending goes through the backend (``POST /api/print``), which checks the
 * print permission and forwards the text with the arguments the admin set up.
 */
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { apiClient, ApiError } from '@/api/client'
import { useBlockStore, type Block } from '@/stores/blocks'
import {
  useDatabaseStore,
  type DatabaseEntry,
  type PropertySchema,
} from '@/stores/database'
import { useUsersStore } from '@/stores/users'
import { useNoticesStore } from '@/stores/notices'
import {
  displayValue,
  getKeyingConfig,
  type RelationKeyResolver,
} from '@/components/editor/blocks/properties/cells/cellUtils'
import {
  DEFAULT_GROUP,
  PREF_GROUP_ORDER,
  PREF_VISIBILITY,
  isSchemaVisible,
  orderSchemaGroups,
  type PropertyVisibility,
} from '@/components/main/propertySectionHelpers'

// ── Text building (pure) ──────────────────────────────────────────────────────

/** Prefix of a property name line. */
export const PROPERTY_INDENT = '...... '

/** Prefix of the title line. */
export const TITLE_PREFIX = '# '

/** Mark of an unchecked to-do or checkbox property; ASCII, so every device prints it. */
export const CHECKBOX_OPEN = '[ ]'

/** Mark of a checked to-do or checkbox property. */
export const CHECKBOX_DONE = '[x]'

/** One property line pair: its name and its displayed value. */
export interface PrintProperty {
  label: string
  value: string
}

/** Block types whose ``content.text`` is printed. */
export const TEXT_BLOCK_TYPES: ReadonlySet<string> = new Set([
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
  'code',
])

/** Block types without text of their own whose children are printed. */
export const CONTAINER_BLOCK_TYPES: ReadonlySet<string> = new Set([
  'layout',
  'column',
  'synched_origin',
  'synched_mirror',
])

// Storage format of an inline mention: @<uuid>|
const MENTION_RE = /@([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\|/g

/** *text* with every inline mention replaced by *titleFor* of its id. */
export function resolveMentions(text: string, titleFor: (id: string) => string): string {
  return text.replace(MENTION_RE, (_, id: string) => titleFor(id))
}

/** *checked* as a checkbox mark. */
export function checkboxMark(checked: boolean): string {
  return checked ? CHECKBOX_DONE : CHECKBOX_OPEN
}

/**
 * The printed value of a checkbox property, from its displayed value.
 *
 * The display writes ``true`` / ``false``, and ``☑`` / ``☐`` in the slots of
 * a timeline; both become checkbox marks. A checkbox without any stored value
 * is unchecked, so it prints as such instead of being left out.
 */
export function checkboxPrintValue(displayed: string): string {
  const trimmed = displayed.trim()
  if (trimmed === '' || trimmed === 'false') return CHECKBOX_OPEN
  if (trimmed === 'true') return CHECKBOX_DONE
  return displayed.split('☑').join(CHECKBOX_DONE).split('☐').join(CHECKBOX_OPEN)
}

/**
 * The print text: title, properties with a value, then the text blocks, each
 * part separated by a blank line.
 */
export function buildQuickPrintText(
  title: string,
  properties: PrintProperty[],
  paragraphs: string[],
): string {
  const parts: string[] = [TITLE_PREFIX + title.replace(/\s*\n\s*/g, ' ').trim()]
  for (const { label, value } of properties) {
    if (!value.trim()) continue
    parts.push(`${PROPERTY_INDENT}${label}\n${value}`)
  }
  for (const paragraph of paragraphs) {
    if (paragraph.trim()) parts.push(paragraph)
  }
  return parts.join('\n\n')
}

/**
 * The texts of the text blocks below *rootId*, in document order.
 *
 * Descends into text blocks (their nested children) and into layouts,
 * columns and synced blocks; a synced mirror is read through its origin.
 * A to-do is prefixed with its checkbox mark; one without text is skipped
 * like any empty block.
 * Sub-pages, databases and media are skipped with everything below them.
 * Trashed blocks are ignored. Each block is visited once, so a mirror that
 * points back up the tree cannot loop.
 */
export async function collectTextBlocks(
  rootId: string,
  childrenOf: (parentId: string) => Promise<Block[]>,
): Promise<string[]> {
  const texts: string[] = []
  const visited = new Set<string>([rootId])

  async function walk(parentId: string): Promise<void> {
    for (const block of await childrenOf(parentId)) {
      if (block.state !== 'active' || visited.has(block.id)) continue
      visited.add(block.id)
      if (TEXT_BLOCK_TYPES.has(block.type)) {
        const text = (block.content?.text as string | undefined) ?? ''
        if (text.trim()) {
          texts.push(block.type === 'to_do'
            ? `${checkboxMark(block.content?.checked === true)} ${text}`
            : text)
        }
        await walk(block.id)
      } else if (block.type === 'synched_mirror') {
        if (block.reference_id && !visited.has(block.reference_id)) {
          visited.add(block.reference_id)
          await walk(block.reference_id)
        }
      } else if (CONTAINER_BLOCK_TYPES.has(block.type)) {
        await walk(block.id)
      }
    }
  }

  await walk(rootId)
  return texts
}

/** File name of the .txt download for a page titled *title*. */
export function quickPrintFilename(title: string): string {
  const base = title.trim().replace(/[^\p{L}\p{N}_-]+/gu, '_').replace(/^_+|_+$/g, '')
  return `${base || 'print'}.txt`
}

// ── Composable ────────────────────────────────────────────────────────────────

interface PrintStatus {
  configured: boolean
  allowed: boolean
}

interface PrintResult {
  ok: boolean
  status: number | null
  body: string
}

export function useQuickPrint() {
  const { t } = useI18n()
  const blockStore = useBlockStore()
  const dbStore = useDatabaseStore()
  const usersStore = useUsersStore()
  const notices = useNoticesStore()

  /** True while a Quick Print is being prepared or sent. */
  const printing = ref(false)

  function titleOf(content: Record<string, unknown> | null | undefined): string {
    return ((content?.title as string | undefined) ?? '').trim() || t('main.untitled')
  }

  async function childrenOf(parentId: string): Promise<Block[]> {
    return blockStore.hasLoadedChildren(parentId)
      ? blockStore.getChildren(parentId)
      : blockStore.fetchChildren(parentId)
  }

  /** The text blocks of *blockId*, mentions resolved to page titles. */
  async function paragraphsOf(blockId: string): Promise<string[]> {
    // Loads the page's children at any depth and the mentioned pages at once.
    await blockStore.fetchTree(blockId, true)
    const texts = await collectTextBlocks(blockId, childrenOf)
    return texts.map((text) =>
      resolveMentions(text, (id) => titleOf(blockStore.blocks[id]?.content)),
    )
  }

  /**
   * Key-value lookups for the keyed relations among *schemas*, or undefined
   * when none is keyed. Key values come from the store, which loading the
   * entry filled; the target schemas are loaded for the key property's type.
   */
  async function keyResolverFor(schemas: PropertySchema[]): Promise<RelationKeyResolver | undefined> {
    const targets = new Set<string>()
    for (const schema of schemas) {
      if (schema.type !== 'relation' || getKeyingConfig(schema) === null) continue
      const target = schema.config?.target_database_id as string | undefined
      if (target) targets.add(target)
    }
    if (targets.size === 0) return undefined
    const byId = new Map<string, PropertySchema>()
    for (const target of targets) {
      try {
        for (const s of await dbStore.ensureSchemas(target)) byId.set(s.id, s)
      } catch {
        // Without the key property the relation prints without key values.
      }
    }
    return {
      schemaFor: (keyPropertyId) => byId.get(keyPropertyId) ?? null,
      valueFor: (keyPropertyId, entryId) => dbStore.getRelationKeyValue(keyPropertyId, entryId),
    }
  }

  /**
   * *schemas* of *entry* as name and displayed value. Checkbox properties
   * print as checkbox marks, unchecked ones included.
   */
  async function propertiesOf(entry: DatabaseEntry, schemas: PropertySchema[]): Promise<PrintProperty[]> {
    await usersStore.loadUsers()
    const keyResolver = await keyResolverFor(schemas)
    const resolveTitle = (id: string): string | null =>
      dbStore.hasRelationEntry(id) ? (dbStore.getRelationTitle(id) || t('main.untitled')) : null
    return schemas.map((schema) => {
      const displayed = displayValue(entry, schema, usersStore.resolveUser, resolveTitle, keyResolver)
      return {
        label: schema.name,
        value: schema.type === 'checkbox' ? checkboxPrintValue(displayed) : displayed,
      }
    })
  }

  function download(text: string, title: string): void {
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = quickPrintFilename(title)
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  /**
   * Send *text* for *blockId* to the printer, or download it when Quick
   * Print does not go to a printer for this account.
   */
  async function deliver(blockId: string, title: string, text: string): Promise<void> {
    const status = await apiClient.get<PrintStatus>('/api/print/status')
    if (!status.configured || !status.allowed) {
      download(text, title)
      return
    }
    const result = await apiClient.post<PrintResult>('/api/print', { block_id: blockId, text })
    if (result.ok) {
      notices.push('quickPrint.printed', { entry: title })
    } else {
      notices.push('quickPrint.failed', {
        reason: result.status === null ? result.body : `HTTP ${result.status}`,
      })
    }
  }

  async function run(job: () => Promise<void>): Promise<void> {
    if (printing.value) return
    printing.value = true
    try {
      await job()
    } catch (e) {
      notices.push('quickPrint.failed', {
        reason: e instanceof ApiError ? e.message : String(e),
      })
    } finally {
      printing.value = false
    }
  }

  /**
   * Quick Print of a table row: *columns* are the view's visible property
   * columns in the view's order (the name column is not among them; the
   * title heads the print).
   */
  function printEntry(databaseId: string, entryId: string, columns: PropertySchema[]): Promise<void> {
    return run(async () => {
      const entry = await dbStore.fetchEntry(databaseId, entryId)
      const title = titleOf(entry.content)
      const [properties, paragraphs] = await Promise.all([
        propertiesOf(entry, columns),
        paragraphsOf(entryId),
      ])
      await deliver(entryId, title, buildQuickPrintText(title, properties, paragraphs))
    })
  }

  /**
   * Quick Print of a page. An entry of a database prints its properties as
   * the property section shows them; any other page prints title and text.
   */
  function printPage(block: Block): Promise<void> {
    return run(async () => {
      const parentId = block.parent_id
      const parent = parentId
        ? (blockStore.blocks[parentId] ?? await blockStore.fetchBlock(parentId))
        : null

      let properties: PrintProperty[] = []
      let title = titleOf(block.content)
      if (parent?.type === 'database' && parentId) {
        const [entry, schemas] = await Promise.all([
          dbStore.fetchEntry(parentId, block.id),
          dbStore.ensureSchemas(parentId),
          blockStore.fetchPreferences(parentId),
        ])
        title = titleOf(entry.content)
        const groupOrder = blockStore.getPreference<string[]>(parentId, PREF_GROUP_ORDER, [])
        const visibility = blockStore.getPreference<Record<string, PropertyVisibility>>(
          parentId, PREF_VISIBILITY, {},
        )
        const ordered = orderSchemaGroups(
          schemas.filter((s) => s.name !== '__name__'),
          groupOrder,
          DEFAULT_GROUP,
          (s) => isSchemaVisible(s, visibility[s.id], entry),
        ).flatMap((group) => group.schemas)
        properties = await propertiesOf(entry, ordered)
      }

      const paragraphs = await paragraphsOf(block.id)
      await deliver(block.id, title, buildQuickPrintText(title, properties, paragraphs))
    })
  }

  return { printing, printEntry, printPage }
}

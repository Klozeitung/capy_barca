/**
 * useExport
 *
 * CSV / XLSX / PDF / ICS export for a database view.
 *
 * All functions close over the reactive parameters supplied at creation time,
 * so they always operate on the currently displayed data and active view. The
 * export is what the view shows: its columns are the view's visible columns,
 * the name column included only while it is shown.
 *
 * Keyed relations
 * ---------------
 * A keyed relation column exports in key order, each linked entry prefixed by
 * its key value. Both the titles and the key values come from the same
 * unpaginated fetch of the target databases, so the export needs no separate
 * resolver round-trip; only the target schemas are added, to know the key
 * property's type and formatting.
 */
import { ref, type ComputedRef } from 'vue'
import * as XLSX from 'xlsx'
import jsPDF from 'jspdf'
import autoTable from 'jspdf-autotable'
import type { PropertySchema, DatabaseEntry, DatabaseView } from '@/stores/database'
import type { Block } from '@/stores/blocks'
import { useDatabaseStore } from '@/stores/database'
import { useUsersStore } from '@/stores/users'
import {
  displayValue,
  getKeyingConfig,
  type RelationKeyResolver,
} from '@/components/editor/blocks/properties/cells/cellUtils'

interface OrderedColumn {
  key: string
  schema: PropertySchema | null
}

/**
 * Build the CSV column header for a property. When a (trimmed) description is
 * present it is appended in a bracketed marker so the exported header carries
 * the description inline, e.g. ``Name [Description: ...]``. The marker label is
 * passed in so it can be sourced from i18n at the call site.
 */
export function buildCsvHeader(
  name: string,
  description: string,
  descriptionPrefix: string,
): string {
  const trimmed = (description ?? '').trim()
  return trimmed ? `${name} [${descriptionPrefix}: ${trimmed}]` : name
}

/**
 * Build the entry-id → title lookup used by relation-type export columns.
 *
 * ``entries`` are the active entries of every target database, as returned by
 * the unpaginated listing, which never contains trashed entries. When
 * ``complete`` is true every target database was loaded in full, so an id that
 * is not among them belongs to a trashed or deleted entry: the lookup returns
 * ``null`` and the export omits the link, just as the relation cell drops the
 * chip. When a target database could not be loaded the lookup cannot tell a
 * missing entry from an unloaded one and returns ``''``, which keeps the raw
 * id rather than silently losing the link.
 *
 * Untitled entries resolve to *untitledLabel*. Pure on purpose, so the mapping
 * is unit-testable without Pinia.
 */
export function buildEntryTitleLookup(
  entries: DatabaseEntry[],
  untitledLabel: string,
  complete: boolean,
): (id: string) => string | null {
  const titleMap = new Map<string, string>()
  for (const e of entries) {
    const title = ((e.content?.title as string | undefined) ?? '').trim() || untitledLabel
    titleMap.set(e.id, title)
  }
  return (id: string) => titleMap.get(id) ?? (complete ? null : '')
}

/**
 * Build the key-value lookups a keyed relation needs for plain-text rendering.
 *
 * Pure on purpose: the composable collects the target databases' schemas and
 * entries from the store and hands them in, which keeps the mapping itself
 * unit-testable and free of Pinia.
 *
 * Both maps are flat across all target databases. Property ids are unique
 * workspace-wide, so a single ``schemaId -> schema`` map cannot collide, and
 * key values are keyed by the pair so two relations may key the same entry on
 * different properties without overwriting each other.
 */
export function buildRelationKeyResolver(
  targetSchemas: PropertySchema[],
  targetEntries: DatabaseEntry[],
): RelationKeyResolver {
  const schemaById = new Map<string, PropertySchema>()
  for (const schema of targetSchemas) schemaById.set(schema.id, schema)

  const valueByPair = new Map<string, Record<string, unknown>>()
  for (const entry of targetEntries) {
    for (const [schemaId, value] of Object.entries(entry.values ?? {})) {
      if (value && typeof value === 'object') {
        valueByPair.set(`${schemaId}:${entry.id}`, value as Record<string, unknown>)
      }
    }
  }

  return {
    schemaFor: (keyPropertyId: string) => schemaById.get(keyPropertyId) ?? null,
    valueFor: (keyPropertyId: string, entryId: string) =>
      valueByPair.get(`${keyPropertyId}:${entryId}`) ?? null,
  }
}

export function useExport(options: {
  orderedColumns:           ComputedRef<OrderedColumn[]>
  filteredAndSortedEntries: ComputedRef<DatabaseEntry[]>
  block:                    ComputedRef<Block | undefined>
  activeView:               ComputedRef<DatabaseView | null>
  schemas:                  ComputedRef<PropertySchema[]>
  isCalendarView:           ComputedRef<boolean>
  /**
   * Header of the name column: the database's custom label, or the localized
   * default. Reactive so that a rename applies to the next export without
   * recreating the composable.
   */
  nameColLabel: ComputedRef<string>
  t: (key: string, ...args: unknown[]) => string
}) {
  const {
    orderedColumns, filteredAndSortedEntries, block, activeView,
    schemas, nameColLabel, t,
  } = options

  const dbStore = useDatabaseStore()
  const usersStore = useUsersStore()

  // ── State ───────────────────────────────────────────────────────────────────

  const showExportMenu = ref(false)

  // ── Helpers ─────────────────────────────────────────────────────────────────

  /**
   * Collect the target databases referenced by the exported relation-type
   * columns.
   *
   * A ``relation`` points at its configured target database; ``parent_item``
   * and ``sub_item`` link entries of the exported database itself.
   *
   * ``fetchEntries`` hits the unpaginated listing, so after this the store holds
   * every linked entry of every target database with its full values — which is
   * why keyed relations need no separate resolver round-trip on this path.
   */
  function relationTargetDatabaseIds(): Set<string> {
    const targetDbIds = new Set<string>()
    for (const c of orderedColumns.value) {
      const columnSchema = c.schema
      if (columnSchema === null) continue
      if (columnSchema.type === 'relation') {
        const tdb = columnSchema.config?.target_database_id as string | undefined
        if (tdb) targetDbIds.add(tdb)
      } else if (columnSchema.type === 'parent_item' || columnSchema.type === 'sub_item') {
        const ownDbId = block.value?.id
        if (ownDbId) targetDbIds.add(ownDbId)
      }
    }
    return targetDbIds
  }

  /**
   * Build an entry-id → title resolver for relation-type columns.
   *
   * Relation values store only target entry IDs, so titles must be looked up
   * from the target databases. Those databases are fetched up front (cached by
   * the store) and flattened into a single lookup; see
   * ``buildEntryTitleLookup`` for how links to trashed entries are handled.
   */
  async function buildEntryTitleResolver(): Promise<(id: string) => string | null> {
    const targetEntries: DatabaseEntry[] = []
    let complete = true
    for (const dbId of relationTargetDatabaseIds()) {
      try {
        await dbStore.fetchEntries(dbId)
      } catch {
        // best-effort: without the full listing a missing id cannot be told
        // apart from a trashed one, so unresolved ids keep their raw UUID.
        complete = false
      }
      targetEntries.push(...dbStore.getEntries(dbId))
    }
    return buildEntryTitleLookup(targetEntries, t('main.untitled'), complete)
  }

  /**
   * Build the key-value lookups for the exported keyed relation columns, or
   * ``undefined`` when no exported column is keyed.
   *
   * The entries are already in the store from the title resolver above; only
   * the target databases' schemas have to be added, so the key property's type
   * and its own formatting (date format, number format, select labels) apply
   * exactly as they would in its own column.
   */
  async function buildKeyResolver(): Promise<RelationKeyResolver | undefined> {
    const keyedTargetDbIds = new Set<string>()
    for (const c of orderedColumns.value) {
      const columnSchema = c.schema
      if (columnSchema === null || columnSchema.type !== 'relation') continue
      if (getKeyingConfig(columnSchema) === null) continue
      const tdb = columnSchema.config?.target_database_id as string | undefined
      if (tdb) keyedTargetDbIds.add(tdb)
    }
    if (keyedTargetDbIds.size === 0) return undefined

    const targetSchemas: PropertySchema[] = []
    const targetEntries: DatabaseEntry[] = []
    for (const dbId of keyedTargetDbIds) {
      try {
        targetSchemas.push(...(await dbStore.ensureSchemas(dbId)))
      } catch {
        // best-effort: an unresolved key property degrades the column to the
        // vanilla export instead of failing the whole download.
      }
      targetEntries.push(...dbStore.getEntries(dbId))
    }
    return buildRelationKeyResolver(targetSchemas, targetEntries)
  }

  async function getExportData(): Promise<{ headers: string[]; descriptions: string[]; rows: string[][] }> {
    const cols    = orderedColumns.value
    const headers = cols.map(c => c.schema ? c.schema.name : nameColLabel.value)
    const descriptions = cols.map(c =>
      (c.schema?.config?.description as string | undefined)?.trim() ?? ''
    )

    // Pre-warm the resolvers used by displayValue for relation and user columns.
    await usersStore.loadUsers()
    const resolveTitle = await buildEntryTitleResolver()
    // Runs after the title resolver so the target entries are already loaded.
    const keyResolver = await buildKeyResolver()

    const rows = filteredAndSortedEntries.value.map((entry) =>
      cols.map(c =>
        c.schema
          ? displayValue(entry, c.schema, usersStore.resolveUser, resolveTitle, keyResolver)
          : (entry.content?.title as string | undefined) ?? ''
      )
    )
    return { headers, descriptions, rows }
  }

  function dbFilename(): string {
    const title    = ((block.value?.content?.title as string | undefined) ?? '').trim() || 'database'
    const viewName = (activeView.value?.name ?? '').trim()
    return [title, viewName].filter(Boolean).join('_').replace(/[^a-z0-9_-]/gi, '_')
  }

  function _downloadBlob(content: string, filename: string, mimeType: string): void {
    const blob = new Blob(['\uFEFF' + content], { type: mimeType })
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement('a')
    a.href     = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  // ── Exports ─────────────────────────────────────────────────────────────────

  async function exportCSV(): Promise<void> {
    const { headers, descriptions, rows } = await getExportData()
    const descriptionPrefix = t('db.export.descriptionPrefix')
    const headerRow = headers.map((h, i) => buildCsvHeader(h, descriptions[i] ?? '', descriptionPrefix))
    const escape = (s: string) => `"${s.replace(/"/g, '""')}"`
    const lines  = [headerRow, ...rows].map((row) => row.map(escape).join(','))
    _downloadBlob(lines.join('\r\n'), `${dbFilename()}.csv`, 'text/csv;charset=utf-8;')
    showExportMenu.value = false
  }

  async function exportExcel(): Promise<void> {
    const { headers, rows } = await getExportData()
    const ws = XLSX.utils.aoa_to_sheet([headers, ...rows])
    const wb = XLSX.utils.book_new()
    XLSX.utils.book_append_sheet(wb, ws, 'Data')
    XLSX.writeFile(wb, `${dbFilename()}.xlsx`)
    showExportMenu.value = false
  }

  // jsPDF's built-in fonts use WinAnsi/CP1252 encoding and cannot render
  // characters outside it (arrows, checkbox glyphs, etc.), which come out as
  // garbage like "!'". CSV/XLSX keep the original Unicode; only the PDF path
  // maps the glyphs we emit to ASCII equivalents.
  const PDF_GLYPH_MAP: Record<string, string> = {
    '→': '->',
    '←': '<-',
    '↔': '<->',
    '☑': 'true',
    '☐': 'false',
    '✓': 'true',
    '✗': 'false',
    '∞': 'inf',
  }

  function _pdfSafe(s: string): string {
    let out = s
    for (const [glyph, ascii] of Object.entries(PDF_GLYPH_MAP)) {
      if (out.includes(glyph)) out = out.split(glyph).join(ascii)
    }
    return out
  }

  async function exportPDF(): Promise<void> {
    const { headers, descriptions, rows } = await getExportData()
    const safeHeaders = headers.map(_pdfSafe)
    const safeRows    = rows.map(row => row.map(_pdfSafe))

    // Optional description row, inserted directly beneath the header. Only added
    // when at least one column carries a description; rendered 1pt smaller than
    // the body text and in a dimmed, italic colour.
    const safeDescriptions = descriptions.map(d => _pdfSafe(d))
    const hasDescriptions  = safeDescriptions.some(d => d !== '')
    const body                = hasDescriptions ? [safeDescriptions, ...safeRows] : safeRows
    const descriptionRowIndex = hasDescriptions ? 0 : -1

    const doc      = new jsPDF({ orientation: 'landscape' })
    const title    = ((block.value?.content?.title as string | undefined) ?? t('main.untitled')).trim()
    const viewName = activeView.value?.name ?? ''
    doc.setFontSize(14)
    doc.text(_pdfSafe(viewName ? `${title} \u2013 ${viewName}` : title), 14, 15)
    autoTable(doc, {
      head:               [safeHeaders],
      body,
      startY:             22,
      styles:             { fontSize: 8, cellPadding: 3 },
      headStyles:         { fillColor: [55, 55, 55], textColor: 255, fontStyle: 'bold' },
      alternateRowStyles: { fillColor: [248, 248, 248] },
      // Style the description sub-row: 3pt smaller, dimmed, italic, plain white
      // background so the alternating row shading does not tint it. The hook
      // param is contextually typed as CellHookData by jspdf-autotable.
      didParseCell: (data) => {
        if (data.section === 'body' && data.row.index === descriptionRowIndex) {
          data.cell.styles.fontSize  = 7
          data.cell.styles.textColor = [150, 150, 150]
          data.cell.styles.fontStyle = 'italic'
          data.cell.styles.fillColor = [255, 255, 255]
        }
      },
    })
    doc.save(`${dbFilename()}.pdf`)
    showExportMenu.value = false
  }

  // ── ICS ─────────────────────────────────────────────────────────────────────

  function _isoToIcsValue(iso: string): string {
    if (iso.includes('T')) {
      return iso.replace(/[-:]/g, '').slice(0, 13) + '00'
    }
    return iso.replace(/-/g, '')
  }

  function exportICS(): void {
    const view         = activeView.value
    const dateSchemaId = view?.calendarDateSchemaId
    const dateSchema   = dateSchemaId ? schemas.value.find(s => s.id === dateSchemaId) : null

    const lines: string[] = [
      'BEGIN:VCALENDAR',
      'VERSION:2.0',
      'PRODID:-//CapyBarca//Calendar//EN',
      'CALSCALE:GREGORIAN',
      'METHOD:PUBLISH',
    ]

    const dbTitle = ((block.value?.content?.title as string | undefined) ?? '').trim() || 'database'

    for (const entry of filteredAndSortedEntries.value) {
      const title = ((entry.content?.title as string | undefined) ?? '').trim() || 'Untitled'

      let dtStart = ''
      let dtEnd   = ''

      if (dateSchema) {
        const val    = entry.values[dateSchema.id] as Record<string, unknown> | null
        if (val) {
          const rawStart = (val.start as string | undefined) ?? ''
          const rawEnd   = (val.end   as string | undefined) ?? rawStart
          if (rawStart) {
            const allDay = !rawStart.includes('T')
            if (allDay) {
              dtStart = `DTSTART;VALUE=DATE:${_isoToIcsValue(rawStart)}`
              dtEnd   = `DTEND;VALUE=DATE:${_isoToIcsValue(rawEnd)}`
            } else {
              dtStart = `DTSTART:${_isoToIcsValue(rawStart)}`
              dtEnd   = `DTEND:${_isoToIcsValue(rawEnd || rawStart)}`
            }
          }
        }
      }

      if (!dtStart) continue

      lines.push('BEGIN:VEVENT')
      lines.push(`UID:${entry.id}@capybarca`)
      lines.push(dtStart)
      lines.push(dtEnd)
      lines.push(`SUMMARY:${title.replace(/\n/g, '\\n')}`)
      lines.push(`CATEGORIES:${dbTitle}`)
      lines.push('END:VEVENT')
    }

    lines.push('END:VCALENDAR')

    const content = lines.join('\r\n')
    const blob    = new Blob([content], { type: 'text/calendar;charset=utf-8;' })
    const url     = URL.createObjectURL(blob)
    const a       = document.createElement('a')
    a.href        = url
    a.download    = `${dbFilename()}.ics`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
    showExportMenu.value = false
  }

  // ── Public API ──────────────────────────────────────────────────────────────

  return {
    showExportMenu,
    exportCSV,
    exportExcel,
    exportPDF,
    exportICS,
  }
}

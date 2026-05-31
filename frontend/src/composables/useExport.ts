/**
 * useExport
 *
 * CSV / XLSX / PDF / ICS export for a database view.
 *
 * All functions close over the reactive parameters supplied at creation time,
 * so they always operate on the currently displayed data and active view.
 */
import { ref, type ComputedRef } from 'vue'
import * as XLSX from 'xlsx'
import jsPDF from 'jspdf'
import autoTable from 'jspdf-autotable'
import type { PropertySchema, DatabaseEntry, DatabaseView } from '@/stores/database'
import type { Block } from '@/stores/blocks'
import { displayValue } from '@/components/editor/blocks/properties/cells/cellUtils'

interface OrderedColumn {
  key: string
  schema: PropertySchema | null
}

export function useExport(options: {
  orderedColumns:           ComputedRef<OrderedColumn[]>
  filteredAndSortedEntries: ComputedRef<DatabaseEntry[]>
  block:                    ComputedRef<Block | undefined>
  activeView:               ComputedRef<DatabaseView | null>
  schemas:                  ComputedRef<PropertySchema[]>
  isCalendarView:           ComputedRef<boolean>
  /** Pass the result of t('db.nameColumn') and t('main.untitled') at call site. */
  nameColLabel: string
  t: (key: string, ...args: unknown[]) => string
}) {
  const {
    orderedColumns, filteredAndSortedEntries, block, activeView,
    schemas, nameColLabel, t,
  } = options

  // ── State ───────────────────────────────────────────────────────────────────

  const showExportMenu = ref(false)

  // ── Helpers ─────────────────────────────────────────────────────────────────

  function getExportData(): { headers: string[]; rows: string[][] } {
    const cols    = orderedColumns.value
    const headers = cols.map(c => c.schema ? c.schema.name : nameColLabel)
    const rows    = filteredAndSortedEntries.value.map((entry) =>
      cols.map(c =>
        c.schema
          ? displayValue(entry, c.schema)
          : (entry.content?.title as string | undefined) ?? ''
      )
    )
    return { headers, rows }
  }

  function dbFilename(): string {
    const title    = ((block.value?.content?.title as string | undefined) ?? '').trim() || 'database'
    const viewName = (activeView.value?.name ?? '').trim()
    return [title, viewName].filter(Boolean).join('_').replace(/[^a-z0-9_\-]/gi, '_')
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

  function exportCSV(): void {
    const { headers, rows } = getExportData()
    const escape = (s: string) => `"${s.replace(/"/g, '""')}"`
    const lines  = [headers, ...rows].map((row) => row.map(escape).join(','))
    _downloadBlob(lines.join('\r\n'), `${dbFilename()}.csv`, 'text/csv;charset=utf-8;')
    showExportMenu.value = false
  }

  function exportExcel(): void {
    const { headers, rows } = getExportData()
    const ws = XLSX.utils.aoa_to_sheet([headers, ...rows])
    const wb = XLSX.utils.book_new()
    XLSX.utils.book_append_sheet(wb, ws, 'Data')
    XLSX.writeFile(wb, `${dbFilename()}.xlsx`)
    showExportMenu.value = false
  }

  function exportPDF(): void {
    const { headers, rows } = getExportData()
    const doc      = new jsPDF({ orientation: 'landscape' })
    const title    = ((block.value?.content?.title as string | undefined) ?? t('main.untitled')).trim()
    const viewName = activeView.value?.name ?? ''
    doc.setFontSize(14)
    doc.text(viewName ? `${title} \u2013 ${viewName}` : title, 14, 15)
    autoTable(doc, {
      head:               [headers],
      body:               rows,
      startY:             22,
      styles:             { fontSize: 8, cellPadding: 3 },
      headStyles:         { fillColor: [55, 55, 55], textColor: 255, fontStyle: 'bold' },
      alternateRowStyles: { fillColor: [248, 248, 248] },
    })
    doc.save(`${dbFilename()}.pdf`)
    showExportMenu.value = false
  }

  // ── ICS (#53) ───────────────────────────────────────────────────────────────

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

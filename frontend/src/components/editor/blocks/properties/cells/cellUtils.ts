/**
 * cellUtils
 *
 * Shared pure-function utilities for database property cell components.
 *
 * - getTimelineDisplayMode  – read the persisted display mode from a schema
 * - resolveTimelineValue    – extract the "now" (last slot) value from a _timeline object
 * - getAllTimelineRelatedIds – collect all related IDs across all timeline slots
 * - getCellValue            – extract the raw value object for a given schema column
 * - displayValue            – render a human-readable string for any property type
 * - formatDateString        – apply a configured date format pattern
 * - vFocus                  – Vue directive: focus an element when it mounts
 */
import type { Directive } from 'vue'
import type { DatabaseEntry, PropertySchema } from '@/stores/database'

// ── v-focus directive ─────────────────────────────────────────────────────────

/** Focuses an element immediately after it is inserted into the DOM. */
export const vFocus: Directive = {
  mounted(el: HTMLElement) { el.focus() },
}

// ── Timeline display mode ─────────────────────────────────────────────────────

export type TimelineDisplayMode = 'last' | 'all' | 'now' | 'custom'

export function getTimelineDisplayMode(schema: PropertySchema): TimelineDisplayMode {
  return (schema.config?.timelineDisplayMode as TimelineDisplayMode | undefined) ?? 'last'
}

// ── Timeline resolution ───────────────────────────────────────────────────────

export function lastTimelineSlot(
  timeline: Record<string, unknown>,
): Record<string, unknown> | null {
  const keys = Object.keys(timeline)
  if (keys.length === 0) return null
  if (keys.includes('') && keys.length === 1) {
    return timeline[''] as Record<string, unknown>
  }
  const sorted = keys.filter(k => k !== '').sort((a, b) => {
    const aStart = a.startsWith('→') ? '' : a.split('→')[0]
    const bStart = b.startsWith('→') ? '' : b.split('→')[0]
    return aStart < bStart ? -1 : aStart > bStart ? 1 : 0
  })
  if (sorted.length === 0) return (timeline[''] as Record<string, unknown>) ?? null
  return timeline[sorted[sorted.length - 1]] as Record<string, unknown>
}

export function resolveTimelineValue(
  val: Record<string, unknown> | null,
): Record<string, unknown> | null {
  if (!val || !('_timeline' in val)) return val
  const timeline = val._timeline as Record<string, unknown> | null
  if (!timeline || typeof timeline !== 'object') return null
  return lastTimelineSlot(timeline)
}

export function getAllTimelineRelatedIds(raw: Record<string, unknown> | null): string[] {
  if (!raw) return []
  if ('_timeline' in raw) {
    const timeline = raw._timeline as Record<string, unknown> | null
    if (!timeline) return []
    const seen = new Set<string>()
    const result: string[] = []
    for (const slot of Object.values(timeline)) {
      if (slot && typeof slot === 'object') {
        const ids = (slot as Record<string, unknown>).related_ids as string[] | undefined
        for (const id of ids ?? []) {
          if (!seen.has(id)) { seen.add(id); result.push(id) }
        }
      }
    }
    return result
  }
  return (raw.related_ids as string[] | undefined) ?? []
}

// ── Value accessor ────────────────────────────────────────────────────────────

export function getCellValue(
  entry: DatabaseEntry,
  schemaId: string,
  schema?: PropertySchema,
): Record<string, unknown> | null {
  const raw = entry.values[schemaId] ?? null
  if (!raw) return null
  if (schema?.config?.hasTimeline && getTimelineDisplayMode(schema) !== 'all') {
    return resolveTimelineValue(raw)
  }
  return raw
}

export function getRawCellValue(
  entry: DatabaseEntry,
  schemaId: string,
): Record<string, unknown> | null {
  return entry.values[schemaId] ?? null
}

// ── Date formatting ───────────────────────────────────────────────────────────

export function formatDateString(
  isoStr: string,
  format: string,
  includeTime: boolean,
): string {
  if (!isoStr) return ''
  const tIdx = isoStr.indexOf('T')
  const datePart = tIdx !== -1 ? isoStr.slice(0, tIdx) : isoStr
  const timePart = tIdx !== -1 ? isoStr.slice(tIdx + 1, tIdx + 6) : ''
  const parts = datePart.split('-')
  if (parts.length < 3) return isoStr
  const [year, month, day] = parts
  let dateStr: string
  switch (format) {
    case 'YYYY-MM-DD': dateStr = `${year}-${month}-${day}`; break
    case 'YYYY-DD-MM': dateStr = `${year}-${day}-${month}`; break
    case 'MM.DD.YYYY':
    case 'MM-DD-YYYY': dateStr = `${month}.${day}.${year}`; break
    case 'DD.MM.YYYY':
    case 'DD-MM-YYYY':
    default:           dateStr = `${day}.${month}.${year}`; break
  }
  if (includeTime && timePart) return `${dateStr} ${timePart}`
  return dateStr
}

// ── Display value ─────────────────────────────────────────────────────────────

const euroFormatter = new Intl.NumberFormat('de-DE', { style: 'currency', currency: 'EUR' })

export function formatSlotScalar(slotVal: Record<string, unknown>, schema: PropertySchema): string {
  switch (schema.type) {
    case 'text':
      return (slotVal.text as string | undefined) ?? ''
    case 'number': {
      if (slotVal.number === undefined) return ''
      const fmt = (schema.config?.format as string | undefined) ?? 'plain'
      return fmt === 'euro'
        ? euroFormatter.format(Number(slotVal.number))
        : String(slotVal.number)
    }
    case 'checkbox':
      return slotVal.checked ? '☑' : '☐'
    case 'select': {
      const m = (schema.config?.mode as string | undefined) ?? 'single'
      if (m === 'multiple') return ((slotVal.options as string[] | undefined) ?? []).join(', ')
      return (slotVal.option as string | undefined) ?? ''
    }
    case 'date': {
      const start  = (slotVal.start as string | undefined) ?? ''
      const end    = (slotVal.end   as string | undefined) ?? ''
      const fmt    = (schema.config?.dateFormat  as string  | undefined) ?? 'DD.MM.YYYY'
      const time   = (schema.config?.includeTime as boolean | undefined) ?? false
      const hasEnd = (schema.config?.hasEndDate  as boolean | undefined) ?? false
      if (!start) return ''
      const s = formatDateString(start, fmt, time)
      if (hasEnd && end && end !== start) return `${s} → ${formatDateString(end, fmt, time)}`
      return s
    }
    case 'email': case 'phone': case 'url':
      return (slotVal.value as string | undefined) ?? ''
    default:
      return (slotVal.text as string | undefined) ?? ''
  }
}

export function formatPeriodKey(key: string): string {
  if (key === '') return '∞'
  const shorten = (ts: string) => ts.slice(0, 10)
  if (key.startsWith('→')) return `→ ${shorten(key.slice(1))}`
  const [s, e] = key.split('→')
  return e ? `${shorten(s)} → ${shorten(e)}` : `${shorten(s)} →`
}

export function displayValue(
  entry: DatabaseEntry,
  schema: PropertySchema,
  resolveUser?: (userId: string) => string,
): string {
  const raw = getRawCellValue(entry, schema.id)
  if (raw === null || raw === undefined) return ''

  const mode = schema.config?.hasTimeline ? getTimelineDisplayMode(schema) : 'last'

  // ── "all" mode ────────────────────────────────────────────────────────────
  if (mode === 'all' && schema.config?.hasTimeline && '_timeline' in raw) {
    const timeline = raw._timeline as Record<string, unknown>
    const keys = Object.keys(timeline)
    if (keys.length === 0) return ''
    if (keys.length === 1 && keys[0] === '') {
      return formatSlotScalar(timeline[''] as Record<string, unknown>, schema)
    }
    const sorted = keys.slice().sort((a, b) => {
      const as_ = a.startsWith('→') ? '' : a.split('→')[0]
      const bs_ = b.startsWith('→') ? '' : b.split('→')[0]
      return as_ < bs_ ? -1 : as_ > bs_ ? 1 : 0
    })
    return sorted
      .map(k => {
        const sv = formatSlotScalar(timeline[k] as Record<string, unknown>, schema)
        return sv ? `${formatPeriodKey(k)}: ${sv}` : formatPeriodKey(k)
      })
      .filter(Boolean)
      .join(' · ')
  }

  // ── "last" / default mode ─────────────────────────────────────────────────
  const val = schema.config?.hasTimeline ? resolveTimelineValue(raw) : raw
  if (val === null || val === undefined) return ''

  switch (schema.type) {
    case 'text':
      return (val.text as string | undefined) ?? ''

    case 'number': {
      if (val.number === undefined) return ''
      const format = (schema.config?.format as string | undefined) ?? 'plain'
      return format === 'euro'
        ? euroFormatter.format(Number(val.number))
        : String(val.number)
    }

    case 'select': {
      const selectMode = (schema.config?.mode as string | undefined) ?? 'single'
      if (selectMode === 'multiple') {
        return ((val.options as string[] | undefined) ?? []).join(', ')
      }
      return (val.option as string | undefined) ?? ''
    }

    case 'date': {
      const start       = (val.start       as string  | undefined) ?? ''
      const end         = (val.end         as string  | undefined) ?? ''
      const hasEndDate  = (schema.config?.hasEndDate  as boolean | undefined) ?? false
      const includeTime = (schema.config?.includeTime as boolean | undefined) ?? false
      const dateFormat  = (schema.config?.dateFormat  as string  | undefined) ?? 'DD-MM-YYYY'
      if (!start) return ''
      const startFmt = formatDateString(start, dateFormat, includeTime)
      if (hasEndDate && end && end !== start) {
        return `${startFmt} → ${formatDateString(end, dateFormat, includeTime)}`
      }
      return startFmt
    }

    case 'email': case 'phone': case 'url':
      return (val.value as string | undefined) ?? ''

    case 'file': {
      const files = (val.files as Array<{ name: string }> | undefined) ?? []
      return files.map(f => f.name).join(', ')
    }

    case 'id': {
      const prefix  = (schema.config?.prefix   as string | undefined) ?? ''
      const idValue = val.id_value as number | undefined
      return idValue !== undefined ? `${prefix}${idValue}` : ''
    }

    case 'created_by': case 'last_edited_by': {
      // New format: {"user_id": "<uuid>"} – resolved via resolveUser callback.
      // Legacy format: {"username": "<name>"} – displayed as-is for backward compat.
      const userId = val.user_id as string | undefined
      if (userId) return resolveUser ? resolveUser(userId) : userId
      return (val.username as string | undefined) ?? ''
    }

    case 'created_time': case 'last_edited_time': {
      const dt = val.datetime as string | undefined
      if (!dt) return ''
      try { return new Date(dt).toLocaleString() } catch { return dt }
    }

    default:
      return (val.text as string | undefined) ?? ''
  }
}

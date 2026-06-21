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
 * - formatRollupExport      – plain-text rollup rendering (export)
 * - formatFormulaExport     – plain-text formula rendering (export)
 * - formatDateString        – apply a configured date format pattern
 * - resolveDateFormat       – pick the effective display format (property → user → fallback)
 * - formatCanonicalDate     – format a single canonical ISO string (auto time)
 * - maybeFormatRollupDate   – format rollup ISO scalars / ranges, pass through non-dates
 * - vFocus                  – Vue directive: focus an element when it mounts
 */
import type { Directive } from 'vue'
import type { DatabaseEntry, PropertySchema } from '@/stores/database'
import { useAuthStore } from '@/stores/auth'

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

// ── Relation nuance ───────────────────────────────────────────────────────────

export type NuanceOrientation = 'prepended' | 'appended'

export interface NuanceConfig {
  enabled: boolean
  options: Array<{ label: string; color?: string }>
  affix1: string
  affix2: string
  orientation: NuanceOrientation
}

/**
 * Read a relation schema's own nuance config, or ``null`` when nuance is not
 * enabled.  Each schema (base and bilateral mirror) carries its own affixes and
 * orientation; the option set is shared across both sides.  Absent config means
 * the relation renders exactly as before.
 */
export function getNuanceConfig(schema: PropertySchema): NuanceConfig | null {
  const raw = schema.config?.nuance as Record<string, unknown> | undefined
  if (!raw || raw.enabled !== true) return null
  const orientation: NuanceOrientation =
    raw.orientation === 'appended' ? 'appended' : 'prepended'
  return {
    enabled: true,
    options: (raw.options as Array<{ label: string; color?: string }> | undefined) ?? [],
    affix1: typeof raw.affix1 === 'string' ? raw.affix1 : '',
    affix2: typeof raw.affix2 === 'string' ? raw.affix2 : '',
    orientation,
  }
}

/**
 * Read the nuance label stored for a single linked entry within a resolved
 * value or timeline slot.  Returns ``''`` when none is present.
 */
export function nuanceLabelFor(
  slotOrValue: Record<string, unknown> | null,
  relatedId: string,
): string {
  if (!slotOrValue) return ''
  const map = slotOrValue.nuances as Record<string, string> | undefined
  const label = map?.[relatedId]
  return typeof label === 'string' ? label : ''
}

/**
 * Compose the plain-text form of one (optionally) nuanced relation:
 * the affixes bracket the label, and the chip title sits before or after that
 * group per the schema's orientation.  With no label (or no nuance config) the
 * bare title is returned, so non-nuanced relations are unaffected.
 */
export function formatNuancedRelation(
  title: string,
  label: string,
  nuance: NuanceConfig | null,
): string {
  if (!nuance || !label) return title
  const group = [nuance.affix1, label, nuance.affix2]
    .map(s => s.trim())
    .filter(Boolean)
    .join(' ')
  if (!group) return title
  return nuance.orientation === 'appended' ? `${title} ${group}` : `${group} ${title}`
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

/**
 * Sentinel stored in a date property's ``config.dateFormat`` meaning "use the
 * user's global preference". New date properties default to this; existing
 * properties keep whatever explicit token they were saved with.
 */
export const GLOBAL_DATE_FORMAT = 'global'

/** Fallback used when neither a property override nor a user preference exists. */
const FALLBACK_DATE_FORMAT = 'DD.MM.YYYY'

// Canonical ISO shapes produced by the backend (Python datetime.isoformat()):
//   "2026-03-31"  "2026-03-31T14:30:00"  "2026-03-31T00:00:00+00:00"
export const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/
export const ISO_DATETIME_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/

/**
 * Read the current user's preferred date format from the auth store.
 *
 * Guarded so that any call outside an active Pinia instance (e.g. isolated
 * unit tests) degrades gracefully to an empty string rather than throwing.
 */
function currentUserDateFormat(): string {
  try {
    return useAuthStore().dateFormat || ''
  } catch {
    return ''
  }
}

/**
 * Resolve the effective display format for a schema:
 *   1. an explicit per-property token (anything other than the global sentinel)
 *   2. the user's global preference
 *   3. a hard fallback
 *
 * ``userFormat`` may be passed explicitly (e.g. for deterministic exports);
 * otherwise it is read from the auth store.
 */
export function resolveDateFormat(schema: PropertySchema, userFormat?: string): string {
  const local = schema.config?.dateFormat as string | undefined
  if (local && local !== GLOBAL_DATE_FORMAT) return local
  return (userFormat ?? currentUserDateFormat()) || FALLBACK_DATE_FORMAT
}

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

/**
 * Format a single canonical ISO string for display in the given format.
 *
 * The time component is shown only when it is present AND not midnight, so
 * all-day values (stored at T00:00) render as a plain date while genuinely
 * timed values keep their HH:MM. Used for computed values (rollups, formulas)
 * that carry no explicit ``includeTime`` flag.
 */
export function formatCanonicalDate(iso: string, format: string): string {
  if (!iso) return ''
  const tIdx = iso.indexOf('T')
  const timePart = tIdx !== -1 ? iso.slice(tIdx + 1, tIdx + 6) : ''
  const hasMeaningfulTime = timePart !== '' && timePart !== '00:00'
  return formatDateString(iso, format, hasMeaningfulTime)
}

/**
 * Best-effort formatting of a rollup/formula string result that may contain a
 * canonical date. Handles a single ISO value and an "isoA → isoB" range
 * (emitted by the date_range rollup). Non-date strings pass through unchanged.
 */
export function maybeFormatRollupDate(value: string, format: string): string {
  if (!value) return value
  if (value.includes(' → ')) {
    const [a, b] = value.split(' → ')
    if (ISO_DATE_RE.test(a) || ISO_DATETIME_RE.test(a)) {
      return `${formatCanonicalDate(a, format)} → ${formatCanonicalDate(b, format)}`
    }
    return value
  }
  if (ISO_DATETIME_RE.test(value) || ISO_DATE_RE.test(value)) {
    return formatCanonicalDate(value, format)
  }
  return value
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
      const fmt    = resolveDateFormat(schema)
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

const ROLLUP_PERCENT_FUNCTIONS = new Set([
  'percent_empty', 'percent_not_empty', 'percent_checked', 'percent_unchecked',
])

/** Render a rollup cell value as plain text (for export). Mirrors RollupCell. */
export function formatRollupExport(
  value: Record<string, unknown>,
  schema: PropertySchema,
): string {
  if (value.error) return ''
  const result = value.result
  if (result === null || result === undefined) return ''

  // Relation rollups carry {id, title} descriptors – titles are embedded.
  if (value.relation === true) {
    const items = Array.isArray(result) ? result : [result]
    return (items as Array<Record<string, unknown> | null>)
      .map(e => ((e?.title as string | undefined) ?? '').trim())
      .filter(Boolean)
      .join(', ')
  }

  const fmt = resolveDateFormat(schema)

  // show_original / list of raw scalars
  if (Array.isArray(result)) {
    return result
      .map(v => {
        if (v === null || v === undefined) return ''
        if (typeof v === 'number') return String(v)
        if (typeof v === 'boolean') return v ? 'true' : 'false'
        return maybeFormatRollupDate(String(v), fmt)
      })
      .filter(s => s !== '')
      .join(', ')
  }

  // percent_per_option map
  if (typeof result === 'object') {
    return Object.entries(result as Record<string, number>)
      .sort((a, b) => b[1] - a[1])
      .map(([label, pct]) => `${label}: ${Number.isInteger(pct) ? pct : pct.toFixed(1)} %`)
      .join(', ')
  }

  // scalar / percent / boolean / date
  if (typeof result === 'number') {
    const formatted = Number.isInteger(result)
      ? String(result)
      : result.toFixed(2).replace(/\.?0+$/, '')
    const fn = (value.function as string | undefined) ?? ''
    return ROLLUP_PERCENT_FUNCTIONS.has(fn) ? `${formatted} %` : formatted
  }
  if (typeof result === 'boolean') return result ? 'true' : 'false'
  return maybeFormatRollupDate(String(result), fmt)
}

/** Render a formula cell value as plain text (for export). Mirrors FormulaCell. */
export function formatFormulaExport(
  value: Record<string, unknown>,
  schema: PropertySchema,
): string {
  if (value.error) return ''
  const r = value.result
  if (r === null || r === undefined) return ''
  if (typeof r === 'boolean') return r ? 'true' : 'false'
  if (typeof r === 'number') return r.toLocaleString('de-DE', { maximumFractionDigits: 10 })
  if (typeof r === 'string') {
    if (ISO_DATETIME_RE.test(r) || ISO_DATE_RE.test(r)) {
      return formatCanonicalDate(r, resolveDateFormat(schema))
    }
    return r
  }
  return String(r)
}

export function displayValue(
  entry: DatabaseEntry,
  schema: PropertySchema,
  resolveUser?: (userId: string) => string,
  resolveEntryTitle?: (entryId: string) => string,
): string {
  const raw = getRawCellValue(entry, schema.id)
  if (raw === null || raw === undefined) return ''

  const mode = schema.config?.hasTimeline ? getTimelineDisplayMode(schema) : 'last'
  const nuanceCfg = getNuanceConfig(schema)

  // ── "all" mode ────────────────────────────────────────────────────────────
  if (mode === 'all' && schema.config?.hasTimeline && '_timeline' in raw) {
    const timeline = raw._timeline as Record<string, unknown>
    const keys = Object.keys(timeline)
    if (keys.length === 0) return ''

    // Relation-type slots store ``related_ids`` rather than a scalar value, so
    // they must be resolved to entry titles here. ``formatSlotScalar`` has no
    // relation case and no access to the title resolver, which is why timeline
    // relations previously exported the period only, dropping the chips. This
    // mirrors the "last"-mode relation branch below.
    const isRelationType =
      schema.type === 'relation' ||
      schema.type === 'parent_item' ||
      schema.type === 'sub_item'

    const renderSlot = (slot: Record<string, unknown>): string => {
      if (isRelationType) {
        const ids = (slot?.related_ids as string[] | undefined) ?? []
        return ids
          .map(id => {
            const title = resolveEntryTitle?.(id)
            const base = title && title.trim() ? title : id
            return formatNuancedRelation(base, nuanceLabelFor(slot, id), nuanceCfg)
          })
          .filter(Boolean)
          .join(', ')
      }
      return formatSlotScalar(slot, schema)
    }

    if (keys.length === 1 && keys[0] === '') {
      return renderSlot(timeline[''] as Record<string, unknown>)
    }
    const sorted = keys.slice().sort((a, b) => {
      const as_ = a.startsWith('→') ? '' : a.split('→')[0]
      const bs_ = b.startsWith('→') ? '' : b.split('→')[0]
      return as_ < bs_ ? -1 : as_ > bs_ ? 1 : 0
    })
    return sorted
      .map(k => {
        const sv = renderSlot(timeline[k] as Record<string, unknown>)
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
      const dateFormat  = resolveDateFormat(schema)
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

    case 'checkbox':
      return val.checked ? 'true' : 'false'

    case 'relation': case 'parent_item': case 'sub_item': {
      const ids = (val.related_ids as string[] | undefined) ?? []
      if (ids.length === 0) return ''
      return ids
        .map(id => {
          const title = resolveEntryTitle?.(id)
          const base = title && title.trim() ? title : id
          return formatNuancedRelation(base, nuanceLabelFor(val, id), nuanceCfg)
        })
        .join(', ')
    }

    case 'rollup':
      return formatRollupExport(val, schema)

    case 'formula':
      return formatFormulaExport(val, schema)

    default:
      return (val.text as string | undefined) ?? ''
  }
}

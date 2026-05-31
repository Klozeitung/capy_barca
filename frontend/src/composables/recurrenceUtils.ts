/**
 * recurrenceUtils
 *
 * Client-side expansion of recurring calendar entries.
 *
 * A recurring entry has these optional fields inside its date-schema value:
 *   repeat           – 'daily' | 'weekly' | 'monthly' | 'yearly'
 *   repeatInterval   – positive integer (default 1)
 *   repeatUntil      – YYYY-MM-DD end date (open-ended when absent)
 *   repeatExceptions – string[] of YYYY-MM-DD occurrence dates to skip.
 *                      Populated by the "edit/delete this occurrence" flow.
 *
 * expandEntry() returns one RecurOccurrence per occurrence that intersects
 * [windowStart, windowEnd], capped at MAX_OCCURRENCES per entry.
 */
import type { DatabaseEntry, PropertySchema } from '@/stores/database'

// ── Types ─────────────────────────────────────────────────────────────────────

export interface RecurOccurrence {
  /** The master database entry – all metadata lives here. */
  entry: DatabaseEntry
  /** YYYY-MM-DD start of this specific occurrence. */
  startDate: string
  /** YYYY-MM-DD end of this occurrence (= startDate for single-day events). */
  endDate: string
  /**
   * False only for the occurrence at the master's own original start date.
   * All other generated occurrences are virtual (display-only clones).
   */
  isVirtual: boolean
  /** Master's original YYYY-MM-DD start – used for split / truncate ops. */
  masterStartDate: string
}

// ── Internal helpers ──────────────────────────────────────────────────────────

function toIso(d: Date): string {
  return (
    `${d.getFullYear()}-` +
    `${String(d.getMonth() + 1).padStart(2, '0')}-` +
    `${String(d.getDate()).padStart(2, '0')}`
  )
}

function addUnits(dateStr: string, unit: string, count: number): string {
  const [y, mo, d] = dateStr.split('-').map(Number)
  const dt = new Date(y, mo - 1, d)
  switch (unit) {
    case 'daily':   dt.setDate(dt.getDate() + count);          break
    case 'weekly':  dt.setDate(dt.getDate() + count * 7);      break
    case 'monthly': dt.setMonth(dt.getMonth() + count);        break
    case 'yearly':  dt.setFullYear(dt.getFullYear() + count);  break
  }
  return toIso(dt)
}

const MAX_OCCURRENCES = 500

// ── Public API ────────────────────────────────────────────────────────────────

/** True when the entry has an active repeat mode on the given schema. */
export function isRecurringEntry(
  entry:  DatabaseEntry,
  schema: PropertySchema | null,
): boolean {
  if (!schema) return false
  const val = entry.values[schema.id] as Record<string, unknown> | null
  if (!val) return false
  return ((val.repeat as string | undefined) ?? 'none') !== 'none'
}

/**
 * Return the YYYY-MM-DD date one day before dateStr.
 * Used to set repeatUntil when splitting "this and following" from a series.
 */
export function subtractOneDay(dateStr: string): string {
  const [y, mo, d] = dateStr.split('-').map(Number)
  const dt = new Date(y, mo - 1, d)
  dt.setDate(dt.getDate() - 1)
  return toIso(dt)
}

/**
 * Expand a single entry into all occurrences that overlap [windowStart, windowEnd].
 *
 * For non-recurring entries this returns 0 or 1 result.
 * For recurring entries it generates as many occurrences as fit the window.
 */
export function expandEntry(
  entry:       DatabaseEntry,
  schema:      PropertySchema | null,
  windowStart: string,
  windowEnd:   string,
): RecurOccurrence[] {
  if (!schema) return []

  const val = entry.values[schema.id] as Record<string, unknown> | null
  if (!val) return []

  const rawStart = (val.start as string | undefined) ?? ''
  const rawEnd   = (val.end   as string | undefined) ?? ''
  if (!rawStart) return []

  const masterStartDate = rawStart.slice(0, 10)
  const masterEndDate   = rawEnd ? rawEnd.slice(0, 10) : masterStartDate

  const durationDays = Math.round(
    (new Date(masterEndDate).getTime() - new Date(masterStartDate).getTime()) / 86_400_000,
  )

  const repeat = (val.repeat as string | undefined) ?? 'none'

  // ── Non-recurring: single slot ─────────────────────────────────────────────
  if (repeat === 'none') {
    if (masterEndDate >= windowStart && masterStartDate <= windowEnd) {
      return [{
        entry,
        startDate:       masterStartDate,
        endDate:         masterEndDate,
        isVirtual:       false,
        masterStartDate,
      }]
    }
    return []
  }

  // ── Recurring: generate all occurrences in the window ─────────────────────
  const repeatInterval = Math.max(1, (val.repeatInterval as number | undefined) ?? 1)
  const repeatUntil    = (val.repeatUntil as string | undefined) ?? ''
  const exceptions     = new Set((val.repeatExceptions as string[] | undefined) ?? [])

  const hardEnd = (repeatUntil && repeatUntil < windowEnd) ? repeatUntil : windowEnd

  const results: RecurOccurrence[] = []

  for (let n = 0; n < MAX_OCCURRENCES; n++) {
    const occStart = addUnits(masterStartDate, repeat, n * repeatInterval)
    if (occStart > hardEnd) break

    if (exceptions.has(occStart)) continue

    const occEnd = durationDays > 0
      ? addUnits(masterEndDate, repeat, n * repeatInterval)
      : occStart

    // Include occurrence only when it overlaps the display window.
    if (occEnd >= windowStart && occStart <= windowEnd) {
      results.push({
        entry,
        startDate:       occStart,
        endDate:         occEnd,
        isVirtual:       occStart !== masterStartDate,
        masterStartDate,
      })
    }
  }

  return results
}

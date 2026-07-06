/**
 * dateValue
 *
 * Bridges the application's canonical ISO date strings and the ``Date`` objects
 * used by the date-picker control (``components/DatePicker.vue``).
 *
 * All conversions are hardened for the full 1..9999 year range. The JavaScript
 * ``Date`` *constructor* maps a year in 0..99 into 1900..1999 (``new Date(5, …)``
 * yields the year 1905), so:
 *   - Dates are built via ``setFullYear`` rather than the constructor, and
 *   - years are always emitted zero-padded to four digits.
 *
 * This keeps the stored contract intact for antiquity and far-future dates
 * alike:
 *   includeTime === false  ->  "YYYY-MM-DD"
 *   includeTime === true   ->  "YYYY-MM-DDTHH:mm"
 * The four-digit year also preserves the lexicographic ordering that the
 * start/end comparison logic in the cells relies on (e.g. ``end < start``).
 *
 * All values are handled in local time, matching the previous native
 * ``<input type="date">`` / ``datetime-local`` behaviour: the literal
 * year/month/day/hour/minute the user selects is stored verbatim, with no
 * timezone conversion applied.
 */
import { format as formatDateFns } from 'date-fns'
import type { Locale } from 'date-fns'

// Leading group is 1+ digits so four-digit years parse while remaining lenient
// toward any zero-padded low year already stored.
const ISO_RE = /^(\d{1,})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/

/**
 * Parse a canonical ISO date/datetime string into a ``Date``.
 *
 * Returns ``null`` for empty, nullish, or structurally invalid input. Years
 * 1..99 are set via ``setFullYear`` so they are never remapped into the 1900s.
 */
export function parseIsoToDate(iso: string | null | undefined): Date | null {
  if (!iso) return null
  const m = ISO_RE.exec(iso)
  if (!m) return null

  const year = Number(m[1])
  const month = Number(m[2])
  const day = Number(m[3])
  const hour = m[4] !== undefined ? Number(m[4]) : 0
  const minute = m[5] !== undefined ? Number(m[5]) : 0

  if (!year || month < 1 || month > 12 || day < 1 || day > 31) return null

  const d = new Date(0)
  d.setFullYear(year, month - 1, day)
  d.setHours(hour, minute, 0, 0)
  return d
}

/**
 * Format a ``Date`` into a canonical ISO string.
 *
 * The year is zero-padded to four digits (so the year 5 becomes ``"0005-…"``),
 * preserving lexicographic ordering. Returns ``''`` for a nullish date.
 *
 * @param includeTime  When true, append ``THH:mm`` (matching the previous
 *                      ``datetime-local`` contract); otherwise emit a plain date.
 */
export function formatDateToIso(date: Date | null | undefined, includeTime: boolean): string {
  if (!date) return ''

  const year = String(date.getFullYear()).padStart(4, '0')
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  const datePart = `${year}-${month}-${day}`

  if (!includeTime) return datePart

  const hh = String(date.getHours()).padStart(2, '0')
  const mm = String(date.getMinutes()).padStart(2, '0')
  return `${datePart}T${hh}:${mm}`
}

/**
 * Translate an application date-format token into a date-fns pattern for the
 * picker's display and text-entry parsing.
 *
 * The application stores the preference as a token such as ``"DD.MM.YYYY"``,
 * ``"MM.DD.YYYY"``, ``"YYYY-MM-DD"``, ``"YYYY-DD-MM"`` (global user preference)
 * or a per-property hyphen variant like ``"DD-MM-YYYY"``. Separators (``.`` or
 * ``-``) are preserved; only the field letters are mapped to date-fns tokens
 * (``DD`` -> ``dd``, ``YYYY`` -> ``yyyy``; ``MM`` is already the date-fns month
 * token and stays as-is). ``" HH:mm"`` is appended when a time is shown.
 *
 * This governs on-screen display and what the user types only. The picker's
 * value contract stays canonical ISO via ``parseIsoToDate`` / ``formatDateToIso``.
 */
export function dateFnsPatternFor(token: string, includeTime: boolean): string {
  const base = (token || 'DD.MM.YYYY')
    .replace(/YYYY/g, 'yyyy')
    .replace(/DD/g, 'dd')
  return includeTime ? `${base} HH:mm` : base
}

/**
 * Format a ``Date`` into a display string using a date-fns pattern.
 *
 * This is the display counterpart to the ISO bridge above and the single place
 * the picker's on-screen text is produced. It is used as vue-datepicker's
 * ``format`` *function* rather than passing the pattern as a bare string,
 * because in text-input mode the library does not reliably honour a string
 * ``format`` for a blurred field's read display: it can fall back to its
 * built-in default pattern, which shows the US ``MM/dd/yyyy`` order and, when
 * that default carries a time component, leaks a time into a date-only field.
 * Driving the display through this function makes the user's pattern
 * authoritative in every focus state.
 *
 * ``pattern`` is a date-fns pattern as produced by ``dateFnsPatternFor``. The
 * date-fns ``yyyy`` token zero-pads the year to four digits, so antiquity years
 * render consistently with the stored ISO contract (year 5 -> ``0005``).
 * Returns ``''`` for a nullish date, and also for an invalid one (date-fns
 * throws on ``Invalid Date``), so the field degrades to empty rather than
 * surfacing an error.
 */
export function formatDateForDisplay(
  date: Date | null | undefined,
  pattern: string,
  locale?: Locale,
): string {
  if (!date) return ''
  try {
    return formatDateFns(date, pattern, locale ? { locale } : undefined)
  } catch {
    return ''
  }
}

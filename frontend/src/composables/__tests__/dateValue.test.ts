import { describe, it, expect } from 'vitest'
import { parseIsoToDate, formatDateToIso, dateFnsPatternFor, formatDateForDisplay } from '../dateValue'

// The critical property of this module is a faithful ISO <-> Date round-trip
// across the full 1..9999 year range. The JavaScript Date constructor maps
// years 0..99 into 1900..1999, so the 1..99 slice is where a naive
// implementation silently corrupts data; these tests pin that behaviour down.

describe('parseIsoToDate', () => {
  it('returns null for empty / nullish / malformed input', () => {
    expect(parseIsoToDate('')).toBeNull()
    expect(parseIsoToDate(null)).toBeNull()
    expect(parseIsoToDate(undefined)).toBeNull()
    expect(parseIsoToDate('not-a-date')).toBeNull()
    expect(parseIsoToDate('2000-13-01')).toBeNull() // month out of range
    expect(parseIsoToDate('2000-01-00')).toBeNull() // day out of range
  })

  it('parses low years (1..99) without the 1900s remap', () => {
    expect(parseIsoToDate('0001-01-01')!.getFullYear()).toBe(1)
    expect(parseIsoToDate('0005-03-01')!.getFullYear()).toBe(5)
    expect(parseIsoToDate('0050-12-31')!.getFullYear()).toBe(50)
    expect(parseIsoToDate('0099-01-01')!.getFullYear()).toBe(99)
  })

  it('parses date-only and datetime forms', () => {
    const d = parseIsoToDate('2000-01-02')!
    expect([d.getFullYear(), d.getMonth(), d.getDate()]).toEqual([2000, 0, 2])
    expect([d.getHours(), d.getMinutes()]).toEqual([0, 0])

    const dt = parseIsoToDate('2000-01-02T14:30')!
    expect([dt.getHours(), dt.getMinutes()]).toEqual([14, 30])
  })
})

describe('formatDateToIso', () => {
  it('returns empty string for a nullish date', () => {
    expect(formatDateToIso(null, false)).toBe('')
    expect(formatDateToIso(undefined, true)).toBe('')
  })

  it('zero-pads the year to four digits', () => {
    const d = new Date(0)
    d.setFullYear(5, 2, 1)
    d.setHours(0, 0, 0, 0)
    expect(formatDateToIso(d, false)).toBe('0005-03-01')
  })

  it('emits time only when includeTime is set', () => {
    const d = new Date(0)
    d.setFullYear(2000, 0, 2)
    d.setHours(14, 30, 0, 0)
    expect(formatDateToIso(d, false)).toBe('2000-01-02')
    expect(formatDateToIso(d, true)).toBe('2000-01-02T14:30')
  })
})

describe('round-trip across the 1..9999 range', () => {
  const cases: Array<[string, boolean]> = [
    ['0001-01-01', false],
    ['0005-03-01', false],
    ['0050-12-31', false],
    ['0099-01-01', false],
    ['0100-06-15', false],
    ['1999-02-28', false],
    ['2000-01-01T00:01', true],
    ['2024-06-15T23:59', true],
    ['9999-12-31', false],
  ]

  it.each(cases)('%s round-trips unchanged', (iso, includeTime) => {
    expect(formatDateToIso(parseIsoToDate(iso), includeTime)).toBe(iso)
  })
})

// ── dateFnsPatternFor ─────────────────────────────────────────────────────────
//
// The picker's display/parse format is derived from the application's date
// tokens; the value it emits stays canonical ISO regardless. Separators are
// preserved and only the field letters are mapped to date-fns tokens.

describe('dateFnsPatternFor', () => {
  it('maps the European default token', () => {
    expect(dateFnsPatternFor('DD.MM.YYYY', false)).toBe('dd.MM.yyyy')
    expect(dateFnsPatternFor('DD.MM.YYYY', true)).toBe('dd.MM.yyyy HH:mm')
  })

  it('maps the US and ISO tokens', () => {
    expect(dateFnsPatternFor('MM.DD.YYYY', false)).toBe('MM.dd.yyyy')
    expect(dateFnsPatternFor('YYYY-MM-DD', false)).toBe('yyyy-MM-dd')
    expect(dateFnsPatternFor('YYYY-DD-MM', false)).toBe('yyyy-dd-MM')
  })

  it('preserves the hyphen separators of per-property variants', () => {
    expect(dateFnsPatternFor('DD-MM-YYYY', false)).toBe('dd-MM-yyyy')
    expect(dateFnsPatternFor('MM-DD-YYYY', true)).toBe('MM-dd-yyyy HH:mm')
  })

  it('falls back to the European default for empty input', () => {
    expect(dateFnsPatternFor('', false)).toBe('dd.MM.yyyy')
    expect(dateFnsPatternFor('', true)).toBe('dd.MM.yyyy HH:mm')
  })
})

// ── formatDateForDisplay ──────────────────────────────────────────────────────
//
// The picker's on-screen text is produced here (used as vue-datepicker's
// `format` function). The pattern comes from `dateFnsPatternFor`; the critical
// properties are four-digit year padding for antiquity dates and that a
// date-only pattern never emits a time component.

describe('formatDateForDisplay', () => {
  it('returns empty string for a nullish date', () => {
    expect(formatDateForDisplay(null, 'dd.MM.yyyy')).toBe('')
    expect(formatDateForDisplay(undefined, 'dd.MM.yyyy')).toBe('')
  })

  it('returns empty string for an invalid date instead of throwing', () => {
    expect(formatDateForDisplay(new Date('nonsense'), 'dd.MM.yyyy')).toBe('')
  })

  it('renders the user pattern (European default)', () => {
    expect(formatDateForDisplay(parseIsoToDate('2026-03-31'), 'dd.MM.yyyy')).toBe('31.03.2026')
  })

  it('renders US and ISO patterns from the same date', () => {
    const d = parseIsoToDate('2026-03-31')
    expect(formatDateForDisplay(d, 'MM.dd.yyyy')).toBe('03.31.2026')
    expect(formatDateForDisplay(d, 'yyyy-MM-dd')).toBe('2026-03-31')
  })

  it('zero-pads antiquity years to four digits (year 5 -> 0005)', () => {
    expect(formatDateForDisplay(parseIsoToDate('0005-03-01'), 'dd.MM.yyyy')).toBe('01.03.0005')
    expect(formatDateForDisplay(parseIsoToDate('0099-12-31'), 'dd.MM.yyyy')).toBe('31.12.0099')
  })

  it('appends the time only for a datetime pattern', () => {
    const d = parseIsoToDate('2026-03-31T14:30')
    expect(formatDateForDisplay(d, 'dd.MM.yyyy')).toBe('31.03.2026')
    expect(formatDateForDisplay(d, 'dd.MM.yyyy HH:mm')).toBe('31.03.2026 14:30')
  })
})

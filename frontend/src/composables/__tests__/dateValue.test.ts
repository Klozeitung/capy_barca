import { describe, it, expect } from 'vitest'
import { parseIsoToDate, formatDateToIso } from '../dateValue'

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

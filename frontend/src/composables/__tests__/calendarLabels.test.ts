import { describe, it, expect } from 'vitest'
import {
  agendaDayLabel,
  fullDayLabel,
  mondayFirstWeekdayLabels,
  monthYearLabel,
  weekRangeLabel,
} from '@/composables/calendarLabels'

// The exact punctuation of Intl output may shift between ICU releases, so the
// assertions check the parts a reader relies on: names in the right language,
// the numbers, and their presence in one label.

describe('monthYearLabel', () => {
  it('names the month in the UI language', () => {
    expect(monthYearLabel('en', 2031, 3)).toContain('March')
    expect(monthYearLabel('de', 2031, 3)).toContain('März')
  })

  it('treats the month as 1-based and includes the year', () => {
    const label = monthYearLabel('en', 2031, 12)
    expect(label).toContain('December')
    expect(label).toContain('2031')
  })
})

describe('weekRangeLabel', () => {
  it('shows both ends of a week inside one month', () => {
    const label = weekRangeLabel('en', new Date(2031, 2, 10), new Date(2031, 2, 16))
    expect(label).toContain('10')
    expect(label).toContain('16')
    expect(label).toContain('2031')
    expect(label).toContain('Mar')
  })

  it('names both months when the week crosses a month boundary', () => {
    const label = weekRangeLabel('de', new Date(2031, 2, 31), new Date(2031, 3, 6))
    expect(label).toContain('März')
    expect(label).toContain('Apr')
  })
})

describe('fullDayLabel', () => {
  it('includes weekday, month, day and year in the UI language', () => {
    const en = fullDayLabel('en', new Date(2031, 2, 10))
    expect(en).toContain('Monday')
    expect(en).toContain('March')
    expect(en).toContain('10')
    expect(en).toContain('2031')

    const de = fullDayLabel('de', new Date(2031, 2, 10))
    expect(de).toContain('Montag')
    expect(de).toContain('März')
  })
})

describe('agendaDayLabel', () => {
  it('includes weekday and day but not the year', () => {
    const label = agendaDayLabel('en', new Date(2031, 2, 11))
    expect(label).toContain('Tuesday')
    expect(label).toContain('11')
    expect(label).not.toContain('2031')
  })
})

describe('mondayFirstWeekdayLabels', () => {
  it('returns seven names starting with Monday', () => {
    const en = mondayFirstWeekdayLabels('en')
    expect(en).toHaveLength(7)
    expect(en[0]).toMatch(/^Mon/)
    expect(en[6]).toMatch(/^Sun/)

    const de = mondayFirstWeekdayLabels('de')
    expect(de[0]).toMatch(/^Mo/)
    expect(de[6]).toMatch(/^So/)
  })
})

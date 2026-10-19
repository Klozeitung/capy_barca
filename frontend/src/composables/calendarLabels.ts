/**
 * calendarLabels
 *
 * Locale-aware labels for the calendar and agenda views: month titles, period
 * ranges, day headings and weekday column names. All text comes from
 * Intl.DateTimeFormat for the active UI locale, so month and weekday names
 * follow the interface language without separate locale entries.
 *
 * Every function takes the locale explicitly and works on local calendar dates,
 * so it stays a pure function that the views call from their computed values.
 */

/** "October 2026" / "Oktober 2026". *month* is 1-based. */
export function monthYearLabel(locale: string, year: number, month: number): string {
  return new Intl.DateTimeFormat(locale, { month: 'long', year: 'numeric' })
    .format(new Date(year, month - 1, 1))
}

/**
 * Span of a displayed week, e.g. "Oct 12 – 18, 2026" / "12.–18. Okt. 2026".
 * The locale decides which parts the two ends share.
 */
export function weekRangeLabel(locale: string, start: Date, end: Date): string {
  return new Intl.DateTimeFormat(locale, { month: 'short', day: 'numeric', year: 'numeric' })
    .formatRange(start, end)
}

/** Full heading of a single day, e.g. "Monday, October 19, 2026". */
export function fullDayLabel(locale: string, date: Date): string {
  return new Intl.DateTimeFormat(locale, {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
    year: 'numeric',
  }).format(date)
}

/** Day heading in a month-scoped list, e.g. "Monday, Oct 19". */
export function agendaDayLabel(locale: string, date: Date): string {
  return new Intl.DateTimeFormat(locale, { weekday: 'long', month: 'short', day: 'numeric' })
    .format(date)
}

/**
 * Short weekday names in column order, Monday first, e.g. "Mon" … "Sun" /
 * "Mo" … "So". 2024-01-01 is a Monday, so the following six days complete
 * the week.
 */
export function mondayFirstWeekdayLabels(locale: string): string[] {
  const fmt = new Intl.DateTimeFormat(locale, { weekday: 'short' })
  return Array.from({ length: 7 }, (_, i) => fmt.format(new Date(2024, 0, 1 + i)))
}

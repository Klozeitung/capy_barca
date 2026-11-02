/**
 * alertValue
 *
 * Pure helpers for the cells of an ``alerts`` property.
 *
 * The server writes the caller's own alerts into the entry's values as
 * ``{ alerts: AlertItem[] }`` and keeps every time as an absolute instant in
 * UTC (``"2045-07-17T09:30:00Z"``). The date picker works with the
 * application's local ``"YYYY-MM-DDTHH:mm"`` strings, so the conversion
 * between the two lives here, next to the reading of a cell value.
 */
import { formatDateToIso, parseIsoToDate } from '@/composables/dateValue'

/** Property type whose cells hold alerts. */
export const ALERTS_PROPERTY_TYPE = 'alerts'

/** Longest title an alert may carry; the server refuses longer ones. */
export const ALERT_TITLE_MAX_LENGTH = 200

/** One alert as the server sends it. */
export interface AlertItem {
  id: string
  block_id: string
  schema_id: string
  /** Absolute time, ISO 8601 in UTC. */
  at: string
  title: string
  fired_at: string | null
  delivered: boolean
  dismissed_at: string | null
}

/** An alert with what a notification of it shows. */
export interface AlertNotification extends AlertItem {
  block_title: string
  block_icon: string | null
  database_id: string | null
}

/** The alerts of a cell value, ordered by time; empty for anything else. */
export function parseAlerts(value: Record<string, unknown> | null | undefined): AlertItem[] {
  const raw = value?.alerts
  if (!Array.isArray(raw)) return []
  return raw
    .filter((a): a is AlertItem =>
      !!a && typeof a === 'object' && typeof (a as AlertItem).id === 'string'
      && typeof (a as AlertItem).at === 'string')
    .slice()
    .sort((a, b) => Date.parse(a.at) - Date.parse(b.at) || a.id.localeCompare(b.id))
}

/** The cell value holding *alerts*, ordered by time. */
export function alertsCellValue(alerts: AlertItem[]): { alerts: AlertItem[] } {
  return { alerts: parseAlerts({ alerts }) }
}

/** Whether *alert* has not fired yet. */
export function isUpcoming(alert: AlertItem): boolean {
  return alert.fired_at === null
}

/** The next alert that has not fired yet, or null. */
export function nextAlert(alerts: AlertItem[]): AlertItem | null {
  return parseAlerts({ alerts }).find(isUpcoming) ?? null
}

/**
 * A local ``"YYYY-MM-DDTHH:mm"`` value of the date picker as an absolute
 * instant in UTC; '' for an empty or unreadable value.
 */
export function localToUtcIso(local: string): string {
  const date = parseIsoToDate(local)
  if (!date || Number.isNaN(date.getTime())) return ''
  return date.toISOString()
}

/**
 * An absolute instant as the date picker's local ``"YYYY-MM-DDTHH:mm"``
 * value; '' for an empty or unreadable value.
 */
export function utcToLocalIso(utc: string): string {
  const time = Date.parse(utc)
  if (Number.isNaN(time)) return ''
  return formatDateToIso(new Date(time), true)
}

/** Whether the local picker value *local* lies after *now*. */
export function isInFuture(local: string, now: Date = new Date()): boolean {
  const utc = localToUtcIso(local)
  return utc !== '' && Date.parse(utc) > now.getTime()
}

// ── Moved calendar dates ──────────────────────────────────────────────────────

/**
 * Dispatched on ``window`` when the user moved the date of a calendar entry
 * on which they keep alerts of their own. ``AlertShiftDialog`` asks whether
 * those alerts move along.
 */
export const CALENDAR_DATE_MOVED_EVENT = 'capybarca:calendar-date-moved'

export interface CalendarDateMove {
  databaseId: string
  entryId: string
  /** Start before and after the change, local, as the cells show them. */
  oldStart: string
  newStart: string
}

/** How the user's alerts follow a moved date. */
export type AlertShiftMode = 'keep' | 'day' | 'exact'

/** The start of a date value, '' when it has none. */
export function dateStart(value: Record<string, unknown> | null | undefined): string {
  const start = value?.start
  return typeof start === 'string' ? start : ''
}


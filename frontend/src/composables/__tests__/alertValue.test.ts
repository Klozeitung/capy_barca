import { describe, it, expect } from 'vitest'
import {
  alertsCellValue,
  isInFuture,
  localToUtcIso,
  nextAlert,
  parseAlerts,
  utcToLocalIso,
  type AlertItem,
} from '@/composables/alertValue'

function alert(id: string, at: string, firedAt: string | null = null): AlertItem {
  return {
    id, block_id: 'entry-a', schema_id: 'schema-a', at, title: '',
    fired_at: firedAt, delivered: firedAt !== null, dismissed_at: null,
  }
}

describe('alertValue', () => {
  it('reads the alerts of a cell value in time order', () => {
    const value = { alerts: [alert('b', '2045-07-17T10:00:00Z'), alert('a', '2045-07-17T09:00:00Z')] }
    expect(parseAlerts(value).map((a) => a.id)).toEqual(['a', 'b'])
  })

  it('reads anything else as no alerts', () => {
    expect(parseAlerts(null)).toEqual([])
    expect(parseAlerts({ text: 'Value A' })).toEqual([])
    expect(parseAlerts({ alerts: [{ title: 'no id' }] })).toEqual([])
  })

  it('the next alert skips fired ones', () => {
    const alerts = [
      alert('fired', '2045-07-17T08:00:00Z', '2045-07-17T08:00:00Z'),
      alert('later', '2045-07-17T12:00:00Z'),
      alert('soon', '2045-07-17T10:00:00Z'),
    ]
    expect(nextAlert(alerts)?.id).toBe('soon')
    expect(nextAlert([alerts[0]])).toBeNull()
  })

  it('builds a cell value ordered by time', () => {
    const value = alertsCellValue([alert('b', '2045-07-18T00:00:00Z'), alert('a', '2045-07-17T00:00:00Z')])
    expect(value.alerts.map((a) => a.id)).toEqual(['a', 'b'])
  })

  it('round-trips between the picker value and UTC', () => {
    const utc = localToUtcIso('2045-07-17T09:30')
    expect(utc).toMatch(/Z$/)
    expect(utcToLocalIso(utc)).toBe('2045-07-17T09:30')
  })

  it('keeps the year of far dates intact', () => {
    expect(utcToLocalIso(localToUtcIso('2000-07-28T08:00'))).toBe('2000-07-28T08:00')
  })

  it('treats empty or unreadable values as empty', () => {
    expect(localToUtcIso('')).toBe('')
    expect(localToUtcIso('not a date')).toBe('')
    expect(utcToLocalIso('')).toBe('')
  })

  it('decides whether a picker value lies in the future', () => {
    const now = new Date(2045, 6, 17, 9, 30)
    expect(isInFuture('2045-07-17T09:31', now)).toBe(true)
    expect(isInFuture('2045-07-17T09:30', now)).toBe(false)
    expect(isInFuture('', now)).toBe(false)
  })
})

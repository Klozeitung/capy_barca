import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { apiClient } from '@/api/client'
import { useAlertsStore, alertTag, ALERT_NOTICE_KEY, ALERT_NOTICE_UNTITLED_KEY } from '@/stores/alerts'
import { useNoticesStore } from '@/stores/notices'
import { useDatabaseStore } from '@/stores/database'
import { WS_ALERT_EVENT, WS_RESYNC_EVENT } from '@/stores/ws'
import type { AlertItem, AlertNotification, CalendarDateMove } from '@/composables/alertValue'

function alert(id: string, overrides: Partial<AlertNotification> = {}): AlertNotification {
  return {
    id, block_id: 'entry-a', schema_id: 'schema-a', at: '2045-07-17T09:30:00Z', title: '',
    fired_at: null, delivered: false, dismissed_at: null,
    block_title: 'Entry A', block_icon: null, database_id: 'db-a',
    ...overrides,
  }
}

function send(type: string, payload: unknown): void {
  window.dispatchEvent(new CustomEvent(WS_ALERT_EVENT, { detail: { type, payload } }))
}

describe('useAlertsStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  afterEach(() => {
    useAlertsStore().stop()
    vi.restoreAllMocks()
  })

  it('start shows the pending alerts as sticky notices', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue([alert('a', { title: 'Title A' }), alert('b')])

    await useAlertsStore().start(() => {})

    const notices = useNoticesStore().notices
    expect(notices.map((n) => n.key)).toEqual([ALERT_NOTICE_KEY, ALERT_NOTICE_UNTITLED_KEY])
    expect(notices.every((n) => n.sticky)).toBe(true)
    expect(notices[0].params).toEqual({ entry: 'Entry A', title: 'Title A' })
  })

  it('a fired alert becomes a notice that opens its entry and dismisses it', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue([])
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue(undefined)
    const opened: string[] = []
    await useAlertsStore().start((id) => opened.push(id))

    send('alert.fired', alert('a'))
    const notices = useNoticesStore()
    notices.open(notices.notices[0].id)

    expect(opened).toEqual(['entry-a'])
    expect(post).toHaveBeenCalledWith('/api/alerts/a/dismiss')
    expect(notices.notices).toHaveLength(0)
  })

  it('a dismissal from another session closes the notice without asking the server', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue([])
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue(undefined)
    await useAlertsStore().start(() => {})

    send('alert.fired', alert('a'))
    send('alert.dismissed', { id: 'a' })

    expect(useNoticesStore().notices).toHaveLength(0)
    expect(post).not.toHaveBeenCalled()
  })

  it('a change from another session replaces the cell', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue([])
    const db = useDatabaseStore()
    const setCell = vi.spyOn(db, 'setAlertsValue')
    await useAlertsStore().start(() => {})

    send('alert.changed', {
      block_id: 'entry-a', database_id: 'db-a', schema_id: 'schema-a',
      value: { alerts: [alert('a')] },
    })

    expect(setCell).toHaveBeenCalledWith('db-a', 'entry-a', 'schema-a', { alerts: [expect.objectContaining({ id: 'a' })] })
  })

  it('reloads pending alerts after a reconnect', async () => {
    const get = vi.spyOn(apiClient, 'get').mockResolvedValue([])
    await useAlertsStore().start(() => {})

    window.dispatchEvent(new Event(WS_RESYNC_EVENT))

    expect(get).toHaveBeenCalledTimes(2)
  })

  it('create, update and delete rebuild the cell from the answer', async () => {
    const db = useDatabaseStore()
    const setCell = vi.spyOn(db, 'setAlertsValue')
    const store = useAlertsStore()
    const existing: AlertItem = alert('a')

    vi.spyOn(apiClient, 'post').mockResolvedValue(alert('b', { at: '2045-07-17T08:00:00Z' }))
    await store.createAlert('db-a', 'entry-a', '2045-07-17T08:00:00Z', '', [existing])
    expect(setCell).toHaveBeenLastCalledWith('db-a', 'entry-a', 'schema-a', {
      alerts: [expect.objectContaining({ id: 'b' }), expect.objectContaining({ id: 'a' })],
    })

    vi.spyOn(apiClient, 'patch').mockResolvedValue(alert('a', { title: 'Title B' }))
    await store.updateAlert('db-a', existing, { title: 'Title B' }, [existing])
    expect(setCell).toHaveBeenLastCalledWith('db-a', 'entry-a', 'schema-a', {
      alerts: [expect.objectContaining({ id: 'a', title: 'Title B' })],
    })

    vi.spyOn(apiClient, 'delete').mockResolvedValue(undefined)
    await store.deleteAlert('db-a', existing, [existing])
    expect(setCell).toHaveBeenLastCalledWith('db-a', 'entry-a', 'schema-a', { alerts: [] })
  })

  it('deleting an alert closes its notice', async () => {
    vi.spyOn(apiClient, 'delete').mockResolvedValue(undefined)
    const store = useAlertsStore()
    store.notify(alert('a'))
    expect(useNoticesStore().notices[0].tag).toBe(alertTag('a'))

    await store.deleteAlert('db-a', alert('a'), [alert('a')])

    expect(useNoticesStore().notices).toHaveLength(0)
  })

  it('stop ends listening', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue([])
    const store = useAlertsStore()
    await store.start(() => {})
    store.stop()

    send('alert.fired', alert('a'))

    expect(useNoticesStore().notices).toHaveLength(0)
  })

  it('shiftAlerts posts the move and rebuilds the cell from the answer', async () => {
    const moved = alert('a', { at: '2045-07-18T09:30:00Z' })
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue([moved])
    const db = useDatabaseStore()
    vi.spyOn(db, 'getSchemas').mockReturnValue([
      { id: 'schema-a', database_id: 'db-a', name: 'Alerts', type: 'alerts', config: {}, position: 1 },
    ] as never)
    const setCell = vi.spyOn(db, 'setAlertsValue').mockImplementation(() => {})
    const move: CalendarDateMove = {
      databaseId: 'db-a', entryId: 'entry-a',
      oldStart: '2045-07-16T10:00', newStart: '2045-07-17T10:00',
    }

    await useAlertsStore().shiftAlerts(move, 'day')

    expect(post).toHaveBeenCalledWith('/api/alerts/shift', {
      block_id: 'entry-a', old_start: '2045-07-16T10:00', new_start: '2045-07-17T10:00', mode: 'day',
    })
    expect(setCell).toHaveBeenCalledWith('db-a', 'entry-a', 'schema-a', { alerts: [moved] })
  })
})


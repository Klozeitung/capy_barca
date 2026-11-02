/**
 * alerts store
 *
 * The user's own alerts: personal reminders on database entries, kept by the
 * server in their own table and shown in the cells of an ``alerts`` property
 * (see ``AlertsCell``). Every endpoint acts on the caller's alerts only.
 *
 * Writing
 * -------
 * Alerts are created, changed and deleted one at a time through
 * ``/api/alerts``; the regular value endpoint refuses the property type.
 * After each write the entry's cell is rebuilt from the answer in every
 * cached copy of the entry (``useDatabaseStore().setAlertsValue``). The
 * server also sends ``alert.changed`` with the cell value to the user's other
 * connections, which apply it the same way.
 *
 * Moved dates
 * -----------
 * ``shiftAlerts`` moves the user's alerts on an entry along with its moved
 * calendar date, after ``AlertShiftDialog`` asked how.
 *
 * Notifications
 * -------------
 * When an alert falls due the server sends ``alert.fired``. It becomes a
 * notice that stays until the user closes it; opening it goes to the entry.
 * Either way the alert is dismissed on the server, which sends
 * ``alert.dismissed`` to every connection of the user, so the notice closes
 * in the other tabs as well. Alerts that fell due while the user was away
 * are collected from ``/api/alerts/pending`` when ``start`` runs and after
 * every reconnect.
 */
import { defineStore } from 'pinia'
import { apiClient } from '@/api/client'
import { useDatabaseStore } from '@/stores/database'
import { useNoticesStore } from '@/stores/notices'
import { WS_ALERT_EVENT, WS_RESYNC_EVENT, type AlertMessage } from '@/stores/ws'
import {
  alertsCellValue,
  parseAlerts,
  type AlertItem,
  type AlertNotification,
  type CalendarDateMove,
} from '@/composables/alertValue'

/** i18n keys of an alert notice, with and without a title of its own. */
export const ALERT_NOTICE_KEY = 'alerts.notice'
export const ALERT_NOTICE_UNTITLED_KEY = 'alerts.noticeUntitled'

/** Notice tag of the alert *alertId*. */
export function alertTag(alertId: string): string {
  return `alert:${alertId}`
}

/** Payload of ``alert.changed``: the new cell value of one entry. */
interface AlertChange {
  block_id: string
  database_id: string | null
  schema_id: string | null
  value: { alerts: AlertItem[] }
}

export const useAlertsStore = defineStore('alerts', () => {
  let _navigate: ((blockId: string) => void) | null = null
  let _started = false

  // ── Cells ───────────────────────────────────────────────────────────────

  /** Rebuild the cell of *entryId* from *alerts* in every cached copy. */
  function _writeCell(databaseId: string, entryId: string, schemaId: string, alerts: AlertItem[]): void {
    useDatabaseStore().setAlertsValue(databaseId, entryId, schemaId, alertsCellValue(alerts))
  }

  // ── Writes ──────────────────────────────────────────────────────────────

  /**
   * Create an alert on *entryId* at the UTC instant *at*.
   *
   * *current* is what the cell shows now; the new alert is added to it.
   */
  async function createAlert(
    databaseId: string,
    entryId: string,
    at: string,
    title: string,
    current: AlertItem[],
  ): Promise<AlertItem> {
    const alert = await apiClient.post<AlertItem>('/api/alerts', { block_id: entryId, at, title })
    _writeCell(databaseId, entryId, alert.schema_id, [...current.filter((a) => a.id !== alert.id), alert])
    return alert
  }

  /** Change the time or the title of an alert. */
  async function updateAlert(
    databaseId: string,
    alert: AlertItem,
    changes: { at?: string; title?: string },
    current: AlertItem[],
  ): Promise<AlertItem> {
    const updated = await apiClient.patch<AlertItem>(`/api/alerts/${alert.id}`, changes)
    _writeCell(databaseId, alert.block_id, updated.schema_id,
      current.map((a) => (a.id === updated.id ? updated : a)))
    return updated
  }

  /** Delete an alert. */
  async function deleteAlert(databaseId: string, alert: AlertItem, current: AlertItem[]): Promise<void> {
    await apiClient.delete<void>(`/api/alerts/${alert.id}`)
    _writeCell(databaseId, alert.block_id, alert.schema_id, current.filter((a) => a.id !== alert.id))
    useNoticesStore().dismissTag(alertTag(alert.id))
  }

  /**
   * Move the user's alerts on *move*'s entry along with its moved date:
   * ``day`` keeps each alert's time of day and distance in days, ``exact``
   * its exact distance. The server marks alerts that end up in the past as
   * delivered without a notice.
   */
  async function shiftAlerts(move: CalendarDateMove, mode: 'day' | 'exact'): Promise<void> {
    const alerts = await apiClient.post<AlertItem[]>('/api/alerts/shift', {
      block_id: move.entryId,
      old_start: move.oldStart,
      new_start: move.newStart,
      mode,
    })
    const schema = useDatabaseStore().getSchemas(move.databaseId).find((s) => s.type === 'alerts')
    if (schema) _writeCell(move.databaseId, move.entryId, schema.id, alerts)
  }

  /** Close a delivered alert on the server; failures are left for the next load. */
  async function dismissAlert(alertId: string): Promise<void> {
    try {
      await apiClient.post<AlertItem>(`/api/alerts/${alertId}/dismiss`)
    } catch {
      // The alert stays pending and comes back with the next load.
    }
  }

  // ── Notices ─────────────────────────────────────────────────────────────

  /** Show *alert* as a notice that stays until the user closes it. */
  function notify(alert: AlertNotification): void {
    const params: Record<string, string> = { entry: alert.block_title, title: alert.title }
    useNoticesStore().push(
      alert.title ? ALERT_NOTICE_KEY : ALERT_NOTICE_UNTITLED_KEY,
      params,
      {
        sticky: true,
        tag: alertTag(alert.id),
        icon: 'mdi:bell-ring-outline',
        onOpen: () => _navigate?.(alert.block_id),
        onClose: () => { void dismissAlert(alert.id) },
      },
    )
  }

  /** Load the alerts that fell due and were not dismissed yet. */
  async function loadPending(): Promise<void> {
    let pending: AlertNotification[]
    try {
      pending = await apiClient.get<AlertNotification[]>('/api/alerts/pending')
    } catch {
      return
    }
    for (const alert of pending) notify(alert)
  }

  // ── Live messages ───────────────────────────────────────────────────────

  function _onAlertMessage(e: Event): void {
    const message = (e as CustomEvent<AlertMessage>).detail
    switch (message.type) {
      case 'alert.fired':
        notify(message.payload as AlertNotification)
        return
      case 'alert.dismissed':
        useNoticesStore().dismissTag(alertTag((message.payload as { id: string }).id))
        return
      case 'alert.changed': {
        const change = message.payload as AlertChange
        if (!change.database_id || !change.schema_id) return
        _writeCell(change.database_id, change.block_id, change.schema_id, parseAlerts(change.value))
        return
      }
    }
  }

  function _onResync(): void {
    void loadPending()
  }

  /**
   * Begin listening for alerts and show those that are pending. *navigate*
   * opens an entry when the user opens its notice. Calling it again only
   * replaces *navigate*.
   */
  function start(navigate: (blockId: string) => void): Promise<void> {
    _navigate = navigate
    if (_started) return Promise.resolve()
    _started = true
    window.addEventListener(WS_ALERT_EVENT, _onAlertMessage)
    window.addEventListener(WS_RESYNC_EVENT, _onResync)
    return loadPending()
  }

  /** Stop listening, for instance after signing out. */
  function stop(): void {
    if (!_started) return
    _started = false
    _navigate = null
    window.removeEventListener(WS_ALERT_EVENT, _onAlertMessage)
    window.removeEventListener(WS_RESYNC_EVENT, _onResync)
  }

  return {
    createAlert,
    updateAlert,
    deleteAlert,
    shiftAlerts,
    dismissAlert,
    notify,
    loadPending,
    start,
    stop,
  }
})

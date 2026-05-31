/**
 * WebSocket store.
 *
 * Owns the single WebSocket connection to the backend /ws endpoint and
 * manages the full connection lifecycle: connect, authenticate, receive,
 * reconnect with exponential back-off + jitter, and disconnect.
 *
 * Received block events are re-dispatched as a DOM CustomEvent
 * (`capybarca:block-event`) so that the blocks store (and any future
 * consumer) can react without a direct import dependency on this store.
 *
 * Reconnect behaviour
 * -------------------
 * - Base delay: 1 s, doubles per attempt, capped at 30 s.
 * - Jitter: ±500 ms uniformly distributed, preventing thundering-herd on
 *   future multi-tab scenarios.
 * - Auth failure (close code 4401): no reconnect, status → 'auth_error'.
 * - Deliberate `disconnect()` call: no reconnect.
 * - All other close codes: reconnect indefinitely.
 */

import { ref } from 'vue'
import { defineStore } from 'pinia'

// ── Constants ─────────────────────────────────────────────────────────────────

const RECONNECT_BASE_MS = 1_000
const RECONNECT_MAX_MS = 30_000
const RECONNECT_JITTER_MS = 500
const CLOSE_UNAUTHORIZED = 4401

// ── Types ─────────────────────────────────────────────────────────────────────

export type WsStatus = 'disconnected' | 'connecting' | 'connected' | 'reconnecting' | 'auth_error'

export interface BlockEventPayload {
  event_id: string | null
  event_type: string
  block_id: string | null
  before: unknown
  after: unknown
  created_at: string
}

export const WS_BLOCK_EVENT = 'capybarca:block-event'

// ── Store ─────────────────────────────────────────────────────────────────────

export const useWsStore = defineStore('ws', () => {
  const status = ref<WsStatus>('disconnected')
  const lastEventAt = ref<string | null>(null)

  let _socket: WebSocket | null = null
  let _reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let _attempt = 0
  let _intentionalClose = false

  // ── URL derivation ────────────────────────────────────────────────────────

  function _wsUrl(): string {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${protocol}//${window.location.host}/ws`
  }

  // ── Reconnect scheduling ──────────────────────────────────────────────────

  function _scheduleReconnect(): void {
    const jitter = Math.random() * RECONNECT_JITTER_MS * 2 - RECONNECT_JITTER_MS
    const delay = Math.min(RECONNECT_BASE_MS * 2 ** _attempt, RECONNECT_MAX_MS) + jitter
    _attempt++
    status.value = 'reconnecting'
    _reconnectTimer = setTimeout(() => {
      _reconnectTimer = null
      _open()
    }, delay)
  }

  function _clearReconnectTimer(): void {
    if (_reconnectTimer !== null) {
      clearTimeout(_reconnectTimer)
      _reconnectTimer = null
    }
  }

  // ── Socket lifecycle ──────────────────────────────────────────────────────

  function _open(): void {
    if (_socket && _socket.readyState < WebSocket.CLOSING) {
      return
    }

    status.value = 'connecting'
    const ws = new WebSocket(_wsUrl())
    _socket = ws

    ws.addEventListener('open', () => {
      _attempt = 0
      status.value = 'connected'
    })

    ws.addEventListener('message', (ev: MessageEvent) => {
      let msg: { type: string; payload?: BlockEventPayload }
      try {
        msg = JSON.parse(ev.data as string)
      } catch {
        return
      }

      if (msg.type === 'block.event' && msg.payload) {
        lastEventAt.value = msg.payload.created_at
        window.dispatchEvent(
          new CustomEvent<BlockEventPayload>(WS_BLOCK_EVENT, { detail: msg.payload }),
        )
      }
      // pong frames are silently consumed
    })

    ws.addEventListener('close', (ev: CloseEvent) => {
      _socket = null

      if (ev.code === CLOSE_UNAUTHORIZED) {
        status.value = 'auth_error'
        return
      }

      if (_intentionalClose) {
        status.value = 'disconnected'
        return
      }

      _scheduleReconnect()
    })

    ws.addEventListener('error', () => {
      // The 'close' event always follows 'error', so reconnect is handled
      // there. We update status here only if still 'connecting' to give
      // the user immediate visual feedback.
      if (status.value === 'connecting') {
        status.value = 'reconnecting'
      }
    })
  }

  // ── Public API ────────────────────────────────────────────────────────────

  function connect(): void {
    _intentionalClose = false
    _clearReconnectTimer()
    _open()
  }

  function disconnect(): void {
    _intentionalClose = true
    _clearReconnectTimer()
    if (_socket) {
      _socket.close()
      _socket = null
    }
    status.value = 'disconnected'
  }

  /** Send a ping frame. No-op when not connected. */
  function ping(): void {
    if (_socket && _socket.readyState === WebSocket.OPEN) {
      _socket.send(JSON.stringify({ type: 'ping' }))
    }
  }

  return {
    status,
    lastEventAt,
    connect,
    disconnect,
    ping,
    // Exposed for testing only
    _wsUrl,
  }
})

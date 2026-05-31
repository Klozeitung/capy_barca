/**
 * useWebSocket composable.
 *
 * Wraps the ws store with Vue lifecycle awareness: calls `connect()` when the
 * component mounts and `disconnect()` when it unmounts. Designed to be used
 * once at the application root (e.g. App.vue or a layout component) so the
 * connection is established for the lifetime of the authenticated session.
 *
 * Re-entrant: calling `connect()` while already connected is a no-op in the
 * store, so multiple component instances are safe (though only one mount point
 * is expected in practice).
 *
 * Usage
 * -----
 * ```ts
 * // In App.vue or a layout component, after authentication:
 * const ws = useWebSocket()
 * // status is reactive: ws.status.value === 'connected'
 * ```
 */

import { onMounted, onUnmounted } from 'vue'
import { useWsStore, type WsStatus, WS_BLOCK_EVENT, type BlockEventPayload } from '@/stores/ws'

export type { WsStatus, BlockEventPayload }
export { WS_BLOCK_EVENT }

export function useWebSocket() {
  const wsStore = useWsStore()

  onMounted(() => {
    wsStore.connect()
  })

  onUnmounted(() => {
    wsStore.disconnect()
  })

  return {
    status: wsStore.status,
    lastEventAt: wsStore.lastEventAt,
    disconnect: wsStore.disconnect,
    ping: wsStore.ping,
  }
}

/**
 * useColumnResize
 *
 * Manages column-resize pointer state and persists widths into the active view.
 *
 * Usage:
 *   const resize = useColumnResize({ views, activeViewId, saveViews })
 *   // In template: @pointerdown.stop.prevent="(e) => resize.startResize(e, key, $el)"
 *   // On unmount:  resize.cleanup()
 */
import { ref, type Ref } from 'vue'
import type { DatabaseView } from '@/stores/database'

export function useColumnResize(options: {
  views: Ref<DatabaseView[]>
  activeViewId: Ref<string>
  saveViews: () => Promise<void>
}) {
  const { views, activeViewId, saveViews } = options

  // ── State ───────────────────────────────────────────────────────────────────

  const colWidths   = ref<Record<string, number>>({})
  const resizingKey = ref<string | null>(null)

  // Private mutable tracking (not reactive – only needed for pointer math).
  let _resizeState: { key: string; startX: number; startW: number } | null = null
  let _resizeEl:    HTMLElement | null = null

  // ── Public API ──────────────────────────────────────────────────────────────

  /** Call whenever the active view changes to load its persisted widths. */
  function syncColWidthsFromView(activeView: DatabaseView | null): void {
    colWidths.value = { ...(activeView?.colWidths ?? {}) }
  }

  function startResize(e: PointerEvent, key: string, thEl: HTMLElement | null): void {
    e.preventDefault()
    e.stopPropagation()
    _resizeState = {
      key,
      startX: e.clientX,
      startW: colWidths.value[key] ?? (thEl?.offsetWidth ?? 140),
    }
    resizingKey.value = key
    const el = e.currentTarget as HTMLElement
    el.setPointerCapture(e.pointerId)
    _resizeEl = el
    el.addEventListener('pointermove', _onMove as EventListener)
    el.addEventListener('pointerup',     _onStop as EventListener, { once: true })
    el.addEventListener('pointercancel', _onStop as EventListener, { once: true })
  }

  function _onMove(e: PointerEvent): void {
    if (!_resizeState) return
    const delta = e.clientX - _resizeState.startX
    colWidths.value[_resizeState.key] = Math.max(80, _resizeState.startW + delta)
  }

  async function _onStop(): Promise<void> {
    if (_resizeEl) {
      _resizeEl.removeEventListener('pointermove', _onMove as EventListener)
      _resizeEl = null
    }
    _resizeState  = null
    resizingKey.value = null
    const view = views.value.find((v) => v.id === activeViewId.value)
    if (view) {
      view.colWidths = { ...colWidths.value }
      await saveViews()
    }
  }

  /** Returns inline style object for a column. */
  function colStyle(key: string): Record<string, string> {
    const w  = colWidths.value[key]
    const px = w ? `${w}px` : '60px'
    return { width: px, minWidth: px }
  }

  /** Detach lingering event listeners on component unmount. */
  function cleanup(): void {
    if (_resizeEl) {
      _resizeEl.removeEventListener('pointermove', _onMove as EventListener)
      _resizeEl = null
    }
  }

  /** Expose for the onUnmounted guard in DatabaseBlock (previously checked _resizeEl). */
  function isResizing(): boolean {
    return _resizeEl !== null
  }

  return {
    colWidths,
    resizingKey,
    syncColWidthsFromView,
    startResize,
    colStyle,
    cleanup,
    isResizing,
  }
}

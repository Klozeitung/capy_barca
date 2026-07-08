/**
 * useEscapeStack
 *
 * A shared, application-wide stack of Escape-key handlers for dismissible
 * overlays (side panels, floating pickers, menus).
 *
 * Problem it solves
 * -----------------
 * Several overlays can be open at once — for example a select / relation
 * picker floating above an open SideView. When each overlay attaches its own
 * ``document`` keydown listener, a single Escape press reaches all of them and
 * closes the wrong one: the background panel instead of the foreground picker.
 *
 * Behaviour
 * ---------
 * Overlays register a handler when they open and unregister when they close.
 * One ``document`` keydown listener (bubble phase, so native controls and
 * third-party popups that consume Escape themselves stay untouched) routes each
 * Escape press to the top-most registered handler only, then stops the event so
 * no stale listener double-handles it. The listener is attached on the first
 * registration and removed again once the stack empties.
 *
 * Usage
 * -----
 * High level, inside a component:
 *
 *   // Registered for the component's whole mounted lifetime (overlay is only
 *   // rendered while open):
 *   useEscapeKey(() => emit('close'))
 *
 *   // Registered only while a reactive flag is truthy (overlay stays mounted
 *   // and toggles its picker):
 *   useEscapeKey(closePicker, isOpen)
 *
 * The low-level push / pop / peek helpers are exported for unit testing.
 */
import { onMounted, onUnmounted, watch, type Ref, type ComputedRef } from 'vue'

export interface EscapeEntry {
  id: symbol
  onEscape: () => void
}

// Module-level singleton stack shared across every overlay in the app.
const stack: EscapeEntry[] = []
let listenerAttached = false

function handleKeydown(e: KeyboardEvent): void {
  if (e.key !== 'Escape') return
  const top = peekEscapeHandler()
  if (!top) return
  // The top-most overlay owns this Escape; stop the event so any remaining
  // listener further up the chain does not also react to it.
  e.preventDefault()
  e.stopPropagation()
  top.onEscape()
}

function attachListener(): void {
  if (listenerAttached) return
  document.addEventListener('keydown', handleKeydown)
  listenerAttached = true
}

function detachListener(): void {
  if (!listenerAttached) return
  document.removeEventListener('keydown', handleKeydown)
  listenerAttached = false
}

/** Return the current top-of-stack entry, or undefined when the stack is empty. */
export function peekEscapeHandler(): EscapeEntry | undefined {
  return stack[stack.length - 1]
}

/** Number of currently registered handlers. Exposed for tests. */
export function escapeStackSize(): number {
  return stack.length
}

/**
 * Push an Escape handler onto the stack and return its id.
 * The returned id must be passed to ``popEscapeHandler`` to remove it.
 */
export function pushEscapeHandler(onEscape: () => void): symbol {
  const id = Symbol('escape-handler')
  stack.push({ id, onEscape })
  attachListener()
  return id
}

/**
 * Remove a previously pushed handler by id. Safe to call more than once and
 * regardless of the entry's position in the stack (out-of-order teardown).
 */
export function popEscapeHandler(id: symbol): void {
  const idx = stack.findIndex((entry) => entry.id === id)
  if (idx !== -1) stack.splice(idx, 1)
  if (stack.length === 0) detachListener()
}

/**
 * Composable that wires an overlay's Escape handler to the shared stack.
 *
 * @param onEscape  Invoked when Escape is pressed while this overlay is the
 *                  top-most registered one.
 * @param isActive  Optional reactive open-state. When omitted the handler is
 *                  registered for the component's whole mounted lifetime, which
 *                  suits overlays that are conditionally rendered only while
 *                  open. When provided the handler is registered while the ref
 *                  is truthy and removed otherwise. In both cases the handler
 *                  is removed on unmount.
 */
export function useEscapeKey(
  onEscape: () => void,
  isActive?: Ref<boolean> | ComputedRef<boolean>,
): void {
  let id: symbol | null = null

  const register = (): void => {
    if (id === null) id = pushEscapeHandler(onEscape)
  }
  const unregister = (): void => {
    if (id !== null) {
      popEscapeHandler(id)
      id = null
    }
  }

  if (isActive) {
    watch(isActive, (active) => (active ? register() : unregister()), { immediate: true })
  } else {
    onMounted(register)
  }

  onUnmounted(unregister)
}

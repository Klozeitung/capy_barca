import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import {
  pushEscapeHandler,
  popEscapeHandler,
  peekEscapeHandler,
  escapeStackSize,
} from '@/composables/useEscapeStack'

// ── Helpers ───────────────────────────────────────────────────────────────────

function press(key: string): KeyboardEvent {
  const evt = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })
  document.dispatchEvent(evt)
  return evt
}

const pressEscape = () => press('Escape')

// ── Tests ─────────────────────────────────────────────────────────────────────
//
// The stack is a module-level singleton, so every test tracks the ids it pushes
// and drains them in afterEach — even on assertion failure — to keep state from
// leaking into the next test.

describe('useEscapeStack', () => {
  let ids: symbol[]

  beforeEach(() => {
    ids = []
  })

  afterEach(() => {
    for (const id of ids) popEscapeHandler(id)
    ids = []
  })

  function push(fn: () => void): symbol {
    const id = pushEscapeHandler(fn)
    ids.push(id)
    return id
  }

  it('starts empty', () => {
    expect(escapeStackSize()).toBe(0)
    expect(peekEscapeHandler()).toBeUndefined()
  })

  it('push adds an entry and peek returns the top', () => {
    const fn = () => {}
    const id = push(fn)
    expect(escapeStackSize()).toBe(1)
    expect(peekEscapeHandler()).toEqual({ id, onEscape: fn })
  })

  it('routes Escape to the top-most handler only', () => {
    const calls: string[] = []
    push(() => calls.push('bottom'))
    push(() => calls.push('top'))
    pressEscape()
    expect(calls).toEqual(['top'])
  })

  it('falls through to the next handler after the top is popped', () => {
    const calls: string[] = []
    push(() => calls.push('bottom'))
    const topId = push(() => calls.push('top'))
    popEscapeHandler(topId)
    pressEscape()
    expect(calls).toEqual(['bottom'])
  })

  it('ignores non-Escape keys', () => {
    const calls: string[] = []
    push(() => calls.push('hit'))
    press('Enter')
    press('a')
    expect(calls).toEqual([])
  })

  it('does nothing when the stack is empty', () => {
    expect(() => pressEscape()).not.toThrow()
  })

  it('pop removes the specific entry regardless of order', () => {
    const a = push(() => {})
    const b = push(() => {})
    popEscapeHandler(a)
    expect(escapeStackSize()).toBe(1)
    expect(peekEscapeHandler()?.id).toBe(b)
  })

  it('pop is idempotent', () => {
    const id = push(() => {})
    popEscapeHandler(id)
    expect(() => popEscapeHandler(id)).not.toThrow()
    expect(escapeStackSize()).toBe(0)
  })

  it('detaches the document listener once the stack empties', () => {
    const calls: string[] = []
    const id = push(() => calls.push('hit'))
    popEscapeHandler(id)
    // With no handlers registered the listener is gone; Escape is a no-op.
    pressEscape()
    expect(calls).toEqual([])
  })

  it('prevents the default action when it handles Escape', () => {
    push(() => {})
    const evt = pressEscape()
    expect(evt.defaultPrevented).toBe(true)
  })

  it('leaves Escape untouched when nothing is registered', () => {
    const evt = pressEscape()
    expect(evt.defaultPrevented).toBe(false)
  })
})

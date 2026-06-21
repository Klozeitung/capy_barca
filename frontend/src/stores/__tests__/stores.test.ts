import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useBlockStore } from '@/stores/blocks'
import { useUiStore } from '@/stores/ui'
import { useDrag } from '@/composables/useDrag'
import { WS_BLOCK_EVENT, useWsStore } from '@/stores/ws'
import { useDatabaseStore } from '@/stores/database'
import type { Block } from '@/stores/blocks'
import type { BlockEventPayload } from '@/stores/ws'

// Mock the API client so tests never touch the network
vi.mock('@/api/client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
  ApiError: class ApiError extends Error {
    constructor(
      public status: number,
      message: string,
    ) {
      super(message)
    }
  },
  API_UNAUTHORIZED_EVENT: 'capybarca:unauthorized',
}))

import { apiClient } from '@/api/client'

// ── Block Store ───────────────────────────────────────────────────────────────

describe('useBlockStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('caches a block after fetchBlock', async () => {
    const block = { id: 'abc', type: 'page', position: 1.0, state: 'active' }
    vi.mocked(apiClient.get).mockResolvedValueOnce(block)

    const store = useBlockStore()
    await store.fetchBlock('abc')

    expect(store.blocks['abc']).toMatchObject(block)
  })

  it('caches children after fetchChildren', async () => {
    const children = [
      { id: 'c1', type: 'page', position: 1.0, state: 'active' },
      { id: 'c2', type: 'page', position: 2.0, state: 'active' },
    ]
    vi.mocked(apiClient.get).mockResolvedValueOnce(children)

    const store = useBlockStore()
    await store.fetchChildren('parent-id')

    expect(store.getChildren('parent-id')).toHaveLength(2)
    expect(store.childrenMap['parent-id']).toEqual(['c1', 'c2'])
  })

  it('skips fetch when children are already cached', async () => {
    const store = useBlockStore()
    store.childrenMap['parent-id'] = ['c1']
    store.blocks['c1'] = { id: 'c1', type: 'page', position: 1.0, state: 'active', parent_id: null, reference_id: null, content: null, icon: null, cover: null }

    await store.fetchChildren('parent-id')

    expect(apiClient.get).not.toHaveBeenCalled()
  })

  it('force-refetches when force=true', async () => {
    const children = [{ id: 'c1', type: 'page', position: 1.0, state: 'active' }]
    vi.mocked(apiClient.get).mockResolvedValueOnce(children)

    const store = useBlockStore()
    store.childrenMap['parent-id'] = ['c1']

    await store.fetchChildren('parent-id', true)

    expect(apiClient.get).toHaveBeenCalledOnce()
  })

  it('setPreference stores value locally and calls API', async () => {
    vi.mocked(apiClient.put).mockResolvedValue({})
    const store = useBlockStore()

    await store.setPreference('block-id', 'folded', true)

    expect(store.getPreference('block-id', 'folded', false)).toBe(true)
    expect(apiClient.put).toHaveBeenCalledWith(
      '/api/blocks/block-id/preferences/folded',
      { value: true },
    )
  })

  it('toggleFolded flips the folded preference', async () => {
    vi.mocked(apiClient.put).mockResolvedValue({})
    const store = useBlockStore()

    await store.toggleFolded('block-id')
    expect(store.getPreference('block-id', 'folded', false)).toBe(true)

    await store.toggleFolded('block-id')
    expect(store.getPreference('block-id', 'folded', false)).toBe(false)
  })

  it('deleteBlock marks affected blocks as trash', async () => {
    vi.mocked(apiClient.delete).mockResolvedValueOnce({ affected: ['b1', 'b2'] })
    const store = useBlockStore()
    store.blocks['b1'] = { id: 'b1', type: 'page', position: 1.0, state: 'active', parent_id: 'p', reference_id: null, content: null, icon: null, cover: null }
    store.blocks['b2'] = { id: 'b2', type: 'page', position: 2.0, state: 'active', parent_id: 'b1', reference_id: null, content: null, icon: null, cover: null }

    await store.deleteBlock('b1', 'p')

    expect(store.blocks['b1'].state).toBe('trash')
    expect(store.blocks['b2'].state).toBe('trash')
  })
})

// ── UI Store ──────────────────────────────────────────────────────────────────

describe('useUiStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  it('initialises sidebarWidth from localStorage', () => {
    localStorage.setItem('capybarca-sidebar-width', '320')
    const store = useUiStore()
    expect(store.sidebarWidth).toBe(320)
  })

  it('clampSidebarWidth clamps to min', () => {
    const store = useUiStore()
    store.clampSidebarWidth(0)
    expect(store.sidebarWidth).toBe(store.SIDEBAR_MIN)
  })

  it('clampSidebarWidth clamps to max', () => {
    const store = useUiStore()
    store.clampSidebarWidth(9999)
    expect(store.sidebarWidth).toBe(store.SIDEBAR_MAX)
  })
})

// ── useDrag ───────────────────────────────────────────────────────────────────

describe('useDrag', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('startDrag sets dragging state', () => {
    const drag = useDrag()
    drag.startDrag('block-1', 'parent-1')
    expect(drag.getDragging()).toEqual({ blockId: 'block-1', sourceParentId: 'parent-1', blockType: null, dragMode: 'block' })
  })

  it('endDrag clears dragging state', () => {
    const drag = useDrag()
    drag.startDrag('block-1', 'parent-1')
    drag.endDrag()
    expect(drag.getDragging()).toEqual({ blockId: null, sourceParentId: null, blockType: null, dragMode: 'block' })
  })

  it('dropOnBlock calls moveBlock with correct position', async () => {
    const store = useBlockStore()
    store.childrenMap['target'] = ['c1']
    store.blocks['c1'] = { id: 'c1', type: 'page', position: 5.0, state: 'active', parent_id: 'target', reference_id: null, content: null, icon: null, cover: null }

    const movedBlock = { id: 'block-1', type: 'page', position: 6.0, state: 'active', parent_id: 'target', reference_id: null, content: null, icon: null, cover: null }
    vi.mocked(apiClient.post).mockResolvedValueOnce(movedBlock)

    const drag = useDrag()
    drag.startDrag('block-1', 'parent-1')
    await drag.dropOnBlock('target')

    expect(apiClient.post).toHaveBeenCalledWith(
      '/api/blocks/block-1/move',
      expect.objectContaining({ new_parent_id: 'target', new_position: 6.0 }),
    )
  })

  it('dropBetween computes midpoint position', async () => {
    const movedBlock = { id: 'b', type: 'page', position: 1.5, state: 'active', parent_id: 'p', reference_id: null, content: null, icon: null, cover: null }
    vi.mocked(apiClient.post).mockResolvedValueOnce(movedBlock)

    const drag = useDrag()
    drag.startDrag('b', 'old-parent')
    await drag.dropBetween('p', 1.0, 2.0)

    expect(apiClient.post).toHaveBeenCalledWith(
      '/api/blocks/b/move',
      expect.objectContaining({ new_position: 1.5 }),
    )
  })
})

// ── WS Store ──────────────────────────────────────────────────────────────────

class MockWs {
  static instance: MockWs | null = null
  static lastUrl: string | null = null
  static creationCount: number = 0

  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSING = 2
  static readonly CLOSED = 3

  readyState: number = MockWs.CONNECTING
  private listeners: Record<string, ((ev: Event) => void)[]> = {}

  constructor(url: string) {
    MockWs.lastUrl = url
    MockWs.instance = this
    MockWs.creationCount++
  }

  addEventListener(event: string, cb: (ev: Event) => void): void {
    if (!this.listeners[event]) this.listeners[event] = []
    this.listeners[event].push(cb)
  }

  send(_data: string): void {}

  close(code = 1000): void {
    this.readyState = MockWs.CLOSED
    this._emit('close', { code, wasClean: code === 1000 } as CloseEvent)
  }

  _open(): void {
    this.readyState = MockWs.OPEN
    this._emit('open', new Event('open'))
  }

  _message(data: unknown): void {
    this._emit('message', { data: JSON.stringify(data) } as MessageEvent)
  }

  _closeWithCode(code: number): void {
    this.readyState = MockWs.CLOSED
    this._emit('close', { code, wasClean: false } as CloseEvent)
  }

  _error(): void {
    this._emit('error', new Event('error'))
  }

  _emit(event: string, ev: Event): void {
    for (const cb of this.listeners[event] ?? []) {
      cb(ev)
    }
  }
}

describe('useWsStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    MockWs.instance = null
    MockWs.lastUrl = null
    MockWs.creationCount = 0
    vi.stubGlobal('WebSocket', MockWs)
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('initial status is disconnected', () => {
    const store = useWsStore()
    expect(store.status).toBe('disconnected')
  })

  it('connect() sets status to connecting', () => {
    const store = useWsStore()
    store.connect()
    expect(store.status).toBe('connecting')
  })

  it('status becomes connected on socket open', () => {
    const store = useWsStore()
    store.connect()
    MockWs.instance!._open()
    expect(store.status).toBe('connected')
  })

  it('uses wss:// protocol when page is https', () => {
    Object.defineProperty(window, 'location', {
      value: { protocol: 'https:', host: 'localhost:5173' },
      writable: true,
    })
    const store = useWsStore()
    expect(store._wsUrl()).toBe('wss://localhost:5173/ws')
    Object.defineProperty(window, 'location', {
      value: { protocol: 'http:', host: 'localhost:5173' },
      writable: true,
    })
  })

  it('uses ws:// protocol when page is http', () => {
    const store = useWsStore()
    expect(store._wsUrl()).toMatch(/^ws:\/\//)
  })

  it('status becomes reconnecting on unexpected close', () => {
    const store = useWsStore()
    store.connect()
    MockWs.instance!._open()
    MockWs.instance!._closeWithCode(1006)
    expect(store.status).toBe('reconnecting')
  })

  it('does not reconnect on auth failure (4401)', () => {
    const store = useWsStore()
    store.connect()
    const countBefore = MockWs.creationCount
    MockWs.instance!._closeWithCode(4401)
    expect(store.status).toBe('auth_error')
    vi.runAllTimers()
    expect(MockWs.creationCount).toBe(countBefore)
  })

  it('reconnects after delay on unexpected close', () => {
    const store = useWsStore()
    store.connect()
    const first = MockWs.instance!
    first._open()
    first._closeWithCode(1006)
    expect(store.status).toBe('reconnecting')
    vi.runAllTimers()
    expect(MockWs.instance).not.toBe(first)
    expect(MockWs.instance).not.toBeNull()
  })

  it('disconnect() sets status to disconnected and does not reconnect', () => {
    const store = useWsStore()
    store.connect()
    MockWs.instance!._open()
    const countBefore = MockWs.creationCount
    store.disconnect()
    expect(store.status).toBe('disconnected')
    vi.runAllTimers()
    expect(MockWs.creationCount).toBe(countBefore)
  })

  it('block.event message dispatches DOM custom event', () => {
    const store = useWsStore()
    store.connect()
    MockWs.instance!._open()

    const received: CustomEvent[] = []
    const handler = (e: Event) => received.push(e as CustomEvent)
    window.addEventListener(WS_BLOCK_EVENT, handler)

    MockWs.instance!._message({
      type: 'block.event',
      payload: {
        event_id: 'eid-1',
        event_type: 'created',
        block_id: 'bid-1',
        before: null,
        after: { id: 'bid-1' },
        created_at: '2025-01-01T00:00:00Z',
      },
    })

    expect(received).toHaveLength(1)
    expect(received[0].detail.event_type).toBe('created')
    expect(received[0].detail.block_id).toBe('bid-1')

    window.removeEventListener(WS_BLOCK_EVENT, handler)
  })

  it('lastEventAt is updated on block.event', () => {
    const store = useWsStore()
    store.connect()
    MockWs.instance!._open()

    MockWs.instance!._message({
      type: 'block.event',
      payload: {
        event_id: null,
        event_type: 'moved',
        block_id: 'bid-2',
        before: null,
        after: null,
        created_at: '2025-06-01T12:00:00Z',
      },
    })

    expect(store.lastEventAt).toBe('2025-06-01T12:00:00Z')
  })

  it('malformed JSON message is silently ignored', () => {
    const store = useWsStore()
    store.connect()
    const ws = MockWs.instance!
    ws._open()
    ws._emit('message', { data: 'not-json' } as MessageEvent)
    expect(store.status).toBe('connected')
  })
})

// ── useBlockStore._applyEvent ─────────────────────────────────────────────────

function makeBlock(overrides: Partial<Block> = {}): Block {
  return {
    id: 'b1',
    parent_id: 'parent',
    reference_id: null,
    type: 'page',
    position: 1.0,
    state: 'active',
    content: null,
    icon: null,
    cover: null,
    ...overrides,
  }
}

function makePayload(
  event_type: string,
  overrides: Partial<BlockEventPayload> = {},
): BlockEventPayload {
  return {
    event_id: null,
    event_type,
    block_id: 'b1',
    before: null,
    after: null,
    created_at: '2025-01-01T00:00:00Z',
    ...overrides,
  }
}

describe('useBlockStore._applyEvent', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  // ── created ────────────────────────────────────────────────────────────────

  it('created – stores new block', () => {
    const store = useBlockStore()
    const block = makeBlock()
    store._applyEvent(makePayload('created', { after: block }))
    expect(store.blocks['b1']).toMatchObject({ id: 'b1', type: 'page' })
  })

  it('created – invalidates parent childrenMap', () => {
    const store = useBlockStore()
    store.childrenMap['parent'] = ['old']
    store._applyEvent(makePayload('created', { after: makeBlock() }))
    expect(store.childrenMap['parent']).toBeUndefined()
  })

  // ── content_updated ────────────────────────────────────────────────────────

  it('content_updated – patches existing block', () => {
    const store = useBlockStore()
    store.blocks['b1'] = makeBlock({ content: { title: 'old' } })
    const updated = makeBlock({ content: { title: 'new' } })
    store._applyEvent(makePayload('content_updated', { after: updated }))
    expect(store.blocks['b1'].content).toEqual({ title: 'new' })
  })

  it('content_updated – stores block when not yet cached', () => {
    const store = useBlockStore()
    const block = makeBlock({ content: { title: 'fresh' } })
    store._applyEvent(makePayload('content_updated', { after: block }))
    expect(store.blocks['b1']).toBeDefined()
  })

  // ── appearance_updated ─────────────────────────────────────────────────────

  it('appearance_updated – patches icon and cover', () => {
    const store = useBlockStore()
    store.blocks['b1'] = makeBlock()
    const updated = makeBlock({ icon: 'mdi:star', cover: 'gradient:red' })
    store._applyEvent(makePayload('appearance_updated', { after: updated }))
    expect(store.blocks['b1'].icon).toBe('mdi:star')
    expect(store.blocks['b1'].cover).toBe('gradient:red')
  })

  // ── moved ──────────────────────────────────────────────────────────────────

  it('moved – updates parent_id and position', () => {
    const store = useBlockStore()
    store.blocks['b1'] = makeBlock({ parent_id: 'old-parent' })
    const moved = makeBlock({ parent_id: 'new-parent', position: 2.0 })
    store._applyEvent(
      makePayload('moved', {
        before: makeBlock({ parent_id: 'old-parent' }),
        after: moved,
      }),
    )
    expect(store.blocks['b1'].parent_id).toBe('new-parent')
    expect(store.blocks['b1'].position).toBe(2.0)
  })

  it('moved – invalidates old parent childrenMap', () => {
    const store = useBlockStore()
    store.childrenMap['old-parent'] = ['b1']
    store._applyEvent(
      makePayload('moved', {
        before: makeBlock({ parent_id: 'old-parent' }),
        after: makeBlock({ parent_id: 'new-parent' }),
      }),
    )
    expect(store.childrenMap['old-parent']).toBeUndefined()
  })

  it('moved – invalidates new parent childrenMap', () => {
    const store = useBlockStore()
    store.childrenMap['new-parent'] = []
    store._applyEvent(
      makePayload('moved', {
        before: makeBlock({ parent_id: 'old-parent' }),
        after: makeBlock({ parent_id: 'new-parent' }),
      }),
    )
    expect(store.childrenMap['new-parent']).toBeUndefined()
  })

  // ── state_changed ──────────────────────────────────────────────────────────

  it('state_changed – updates state to trash', () => {
    const store = useBlockStore()
    store.blocks['b1'] = makeBlock({ state: 'active' })
    store._applyEvent(
      makePayload('state_changed', {
        before: { state: 'active' },
        after: { state: 'trash' },
      }),
    )
    expect(store.blocks['b1'].state).toBe('trash')
  })

  it('state_changed – updates state back to active (restore)', () => {
    const store = useBlockStore()
    store.blocks['b1'] = makeBlock({ state: 'trash' })
    store._applyEvent(
      makePayload('state_changed', {
        before: { state: 'trash' },
        after: { state: 'active' },
      }),
    )
    expect(store.blocks['b1'].state).toBe('active')
  })

  it('state_changed – no-op when block not in cache', () => {
    const store = useBlockStore()
    store._applyEvent(
      makePayload('state_changed', { after: { state: 'trash' } }),
    )
    expect(store.blocks['b1']).toBeUndefined()
  })

  // ── purged ─────────────────────────────────────────────────────────────────

  it('purged – removes block from cache', () => {
    const store = useBlockStore()
    store.blocks['b1'] = makeBlock()
    store._applyEvent(makePayload('purged'))
    expect(store.blocks['b1']).toBeUndefined()
  })

  it('purged – invalidates parent childrenMap', () => {
    const store = useBlockStore()
    store.blocks['b1'] = makeBlock({ parent_id: 'parent' })
    store.childrenMap['parent'] = ['b1']
    store._applyEvent(makePayload('purged'))
    expect(store.childrenMap['parent']).toBeUndefined()
  })

  it('purged – no-op when block not in cache', () => {
    const store = useBlockStore()
    store._applyEvent(makePayload('purged'))
    expect(store.blocks['b1']).toBeUndefined()
  })

  // ── reverted ───────────────────────────────────────────────────────────────

  it('reverted – restores previous field values', () => {
    const store = useBlockStore()
    store.blocks['b1'] = makeBlock({ content: { title: 'current' } })
    const reverted = makeBlock({ content: { title: 'original' } })
    store._applyEvent(makePayload('reverted', { after: reverted }))
    expect(store.blocks['b1'].content).toEqual({ title: 'original' })
  })

  // ── unknown event type ─────────────────────────────────────────────────────

  it('unknown event type is silently ignored', () => {
    const store = useBlockStore()
    store._applyEvent(makePayload('future_event_type_we_dont_know_yet'))
  })
})

// ── Database Store ────────────────────────────────────────────────────────────

const makeSchema = (overrides = {}) => ({
  id: 'schema-1',
  database_id: 'db-1',
  name: 'Status',
  type: 'select',
  config: { options: ['Todo', 'Done'] },
  position: 1.0,
  ...overrides,
})

const makeEntry = (overrides = {}) => ({
  id: 'entry-1',
  position: 1.0,
  content: { title: 'Row 1' },
  icon: null,
  state: 'active',
  values: {},
  ...overrides,
})

describe('useDatabaseStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  // ── fetchSchemas ────────────────────────────────────────────────────────────

  it('fetchSchemas caches result under databaseId', async () => {
    const schemas = [makeSchema()]
    vi.mocked(apiClient.get).mockResolvedValueOnce(schemas)

    const store = useDatabaseStore()
    await store.fetchSchemas('db-1')

    expect(store.getSchemas('db-1')).toHaveLength(1)
    expect(store.getSchemas('db-1')[0].name).toBe('Status')
  })

  it('getSchemas returns empty array when not loaded', () => {
    const store = useDatabaseStore()
    expect(store.getSchemas('unknown')).toEqual([])
  })

  // ── createSchema ────────────────────────────────────────────────────────────

  it('createSchema posts to correct endpoint and re-fetches', async () => {
    const created = makeSchema({ id: 'schema-new', name: 'Priority' })
    vi.mocked(apiClient.post).mockResolvedValueOnce(created)
    vi.mocked(apiClient.get).mockResolvedValueOnce([created])

    const store = useDatabaseStore()
    const result = await store.createSchema('db-1', { name: 'Priority', type: 'select' })

    expect(apiClient.post).toHaveBeenCalledWith(
      '/api/databases/db-1/schemas',
      { name: 'Priority', type: 'select' },
    )
    expect(result.name).toBe('Priority')
    expect(store.getSchemas('db-1')).toHaveLength(1)
  })

  // ── updateSchema ────────────────────────────────────────────────────────────

  it('updateSchema patches correct endpoint and re-fetches', async () => {
    const updated = makeSchema({ name: 'Priority' })
    vi.mocked(apiClient.patch).mockResolvedValueOnce(updated)
    vi.mocked(apiClient.get).mockResolvedValueOnce([updated])

    const store = useDatabaseStore()
    const result = await store.updateSchema('db-1', 'schema-1', { name: 'Priority' })

    expect(apiClient.patch).toHaveBeenCalledWith(
      '/api/databases/db-1/schemas/schema-1',
      { name: 'Priority' },
    )
    expect(result.name).toBe('Priority')
  })

  // ── deleteSchema ────────────────────────────────────────────────────────────

  it('deleteSchema calls delete endpoint and re-fetches schemas', async () => {
    vi.mocked(apiClient.delete).mockResolvedValueOnce(undefined)
    vi.mocked(apiClient.get).mockResolvedValueOnce([]) // fetchSchemas

    const store = useDatabaseStore()
    await store.deleteSchema('db-1', 'schema-1')

    expect(apiClient.delete).toHaveBeenCalledWith(
      '/api/databases/db-1/schemas/schema-1',
    )
    expect(store.getSchemas('db-1')).toEqual([])
  })

  it('deleteSchema also re-fetches entries when they are cached', async () => {
    vi.mocked(apiClient.delete).mockResolvedValueOnce(undefined)
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce([])   // fetchSchemas
      .mockResolvedValueOnce([])   // fetchEntries

    const store = useDatabaseStore()
    store.entries['db-1'] = [makeEntry()]

    await store.deleteSchema('db-1', 'schema-1')

    expect(apiClient.get).toHaveBeenCalledTimes(2)
  })

  it('deleteSchema does not re-fetch entries when not cached', async () => {
    vi.mocked(apiClient.delete).mockResolvedValueOnce(undefined)
    vi.mocked(apiClient.get).mockResolvedValueOnce([]) // only fetchSchemas

    const store = useDatabaseStore()
    await store.deleteSchema('db-1', 'schema-1')

    expect(apiClient.get).toHaveBeenCalledTimes(1)
  })

  // ── fetchEntries ────────────────────────────────────────────────────────────

  it('fetchEntries caches entries under databaseId', async () => {
    const entries = [makeEntry(), makeEntry({ id: 'entry-2', position: 2.0 })]
    vi.mocked(apiClient.get).mockResolvedValueOnce(entries)

    const store = useDatabaseStore()
    await store.fetchEntries('db-1')

    expect(store.getEntries('db-1')).toHaveLength(2)
  })

  it('getEntries returns empty array when not loaded', () => {
    const store = useDatabaseStore()
    expect(store.getEntries('unknown')).toEqual([])
  })

  // ── createEntry ─────────────────────────────────────────────────────────────

  it('createEntry posts to correct endpoint and re-fetches', async () => {
    const created = makeEntry({ id: 'entry-new', values: {} })
    vi.mocked(apiClient.post).mockResolvedValueOnce(created)
    vi.mocked(apiClient.get).mockResolvedValueOnce([created])

    const store = useDatabaseStore()
    const result = await store.createEntry('db-1')

    expect(apiClient.post).toHaveBeenCalledWith('/api/databases/db-1/entries')
    expect(result.id).toBe('entry-new')
    expect(store.getEntries('db-1')).toHaveLength(1)
  })

  // ── upsertValue ─────────────────────────────────────────────────────────────

  it('upsertValue calls correct endpoint', async () => {
    vi.mocked(apiClient.put).mockResolvedValueOnce(undefined)

    const store = useDatabaseStore()
    await store.upsertValue('db-1', 'entry-1', 'schema-1', { text: 'Hello' })

    expect(apiClient.put).toHaveBeenCalledWith(
      '/api/databases/db-1/entries/entry-1/values/schema-1',
      { value: { text: 'Hello' } },
    )
  })

  it('upsertValue optimistically patches local entry', async () => {
    vi.mocked(apiClient.put).mockResolvedValueOnce(undefined)

    const store = useDatabaseStore()
    store.entries['db-1'] = [makeEntry({ id: 'entry-1', values: {} })]

    await store.upsertValue('db-1', 'entry-1', 'schema-1', { text: 'World' })

    const row = store.getEntries('db-1').find((e) => e.id === 'entry-1')
    expect(row?.values['schema-1']).toEqual({ text: 'World' })
  })

  it('upsertValue accepts null to clear a cell', async () => {
    vi.mocked(apiClient.put).mockResolvedValueOnce(undefined)

    const store = useDatabaseStore()
    store.entries['db-1'] = [makeEntry({ id: 'entry-1', values: { 'schema-1': { text: 'X' } } })]

    await store.upsertValue('db-1', 'entry-1', 'schema-1', null)

    const row = store.getEntries('db-1').find((e) => e.id === 'entry-1')
    expect(row?.values['schema-1']).toBeNull()
    expect(apiClient.put).toHaveBeenCalledWith(
      '/api/databases/db-1/entries/entry-1/values/schema-1',
      { value: null },
    )
  })

  // ── resolveEntryTitles (#27) ─────────────────────────────────────────────────

  it('resolveEntryTitles posts missing ids and caches descriptors', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce([
      { id: 'e1', title: 'Napoleon', database_id: 'db-1' },
      { id: 'e2', title: null, database_id: 'db-1' },
    ])

    const store = useDatabaseStore()
    await store.resolveEntryTitles('db-1', ['e1', 'e2'])

    expect(apiClient.post).toHaveBeenCalledWith(
      '/api/databases/db-1/entries/resolve-titles',
      { ids: ['e1', 'e2'] },
    )
    expect(store.getRelationTitle('e1')).toBe('Napoleon')
    expect(store.getRelationTitle('e2')).toBeNull() // resolved but untitled
    expect(store.hasRelationEntry('e1')).toBe(true)
    expect(store.hasRelationEntry('e2')).toBe(true)
  })

  it('resolveEntryTitles is a no-op for empty input', async () => {
    const store = useDatabaseStore()
    await store.resolveEntryTitles('db-1', [])
    expect(apiClient.post).not.toHaveBeenCalled()
  })

  it('resolveEntryTitles skips already-resolved ids on the next call', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce([
      { id: 'e1', title: 'A', database_id: 'db-1' },
    ])

    const store = useDatabaseStore()
    await store.resolveEntryTitles('db-1', ['e1'])
    await store.resolveEntryTitles('db-1', ['e1'])

    expect(apiClient.post).toHaveBeenCalledTimes(1)
  })

  it('resolveEntryTitles only requests the still-missing ids', async () => {
    vi.mocked(apiClient.post)
      .mockResolvedValueOnce([{ id: 'e1', title: 'A', database_id: 'db-1' }])
      .mockResolvedValueOnce([{ id: 'e2', title: 'B', database_id: 'db-1' }])

    const store = useDatabaseStore()
    await store.resolveEntryTitles('db-1', ['e1'])
    await store.resolveEntryTitles('db-1', ['e1', 'e2'])

    expect(apiClient.post).toHaveBeenLastCalledWith(
      '/api/databases/db-1/entries/resolve-titles',
      { ids: ['e2'] },
    )
  })

  it('resolveEntryTitles marks unreturned ids resolved but without a descriptor', async () => {
    // Server omits 'gone' (trashed / foreign) — it is resolved but not active.
    vi.mocked(apiClient.post).mockResolvedValueOnce([
      { id: 'e1', title: 'A', database_id: 'db-1' },
    ])

    const store = useDatabaseStore()
    await store.resolveEntryTitles('db-1', ['e1', 'gone'])

    expect(store.isRelationResolved('gone')).toBe(true)
    expect(store.hasRelationEntry('gone')).toBe(false)
    expect(store.getRelationTitle('gone')).toBeNull()
  })

  it('resolveEntryTitles with force re-requests already-resolved ids', async () => {
    vi.mocked(apiClient.post)
      .mockResolvedValueOnce([{ id: 'e1', title: 'Old', database_id: 'db-1' }])
      .mockResolvedValueOnce([{ id: 'e1', title: 'New', database_id: 'db-1' }])

    const store = useDatabaseStore()
    await store.resolveEntryTitles('db-1', ['e1'])
    await store.resolveEntryTitles('db-1', ['e1'], true)

    expect(apiClient.post).toHaveBeenCalledTimes(2)
    expect(store.getRelationTitle('e1')).toBe('New')
  })

  it('resolveEntryTitles with force drops descriptors no longer returned', async () => {
    vi.mocked(apiClient.post)
      .mockResolvedValueOnce([{ id: 'e1', title: 'A', database_id: 'db-1' }])
      .mockResolvedValueOnce([]) // e1 trashed since last resolve

    const store = useDatabaseStore()
    await store.resolveEntryTitles('db-1', ['e1'])
    expect(store.hasRelationEntry('e1')).toBe(true)

    await store.resolveEntryTitles('db-1', ['e1'], true)
    expect(store.hasRelationEntry('e1')).toBe(false)
  })

  it('resolveEntryTitles allows retry after a failed request', async () => {
    vi.mocked(apiClient.post).mockRejectedValueOnce(new Error('network'))

    const store = useDatabaseStore()
    await store.resolveEntryTitles('db-1', ['e1'])
    expect(store.isRelationResolved('e1')).toBe(false) // rolled back

    vi.mocked(apiClient.post).mockResolvedValueOnce([
      { id: 'e1', title: 'A', database_id: 'db-1' },
    ])
    await store.resolveEntryTitles('db-1', ['e1'])
    expect(store.getRelationTitle('e1')).toBe('A')
  })

  it('relation-title getters are empty before any resolve', () => {
    const store = useDatabaseStore()
    expect(store.getRelationTitle('x')).toBeNull()
    expect(store.isRelationResolved('x')).toBe(false)
    expect(store.hasRelationEntry('x')).toBe(false)
  })

  // ── fetchAllDatabases ────────────────────────────────────────────────────────

  it('fetchAllDatabases calls GET /api/databases and stores result', async () => {
    const databases = [
      { id: 'db-1', title: 'Projects' },
      { id: 'db-2', title: null },
    ]
    vi.mocked(apiClient.get).mockResolvedValueOnce(databases)

    const store = useDatabaseStore()
    const result = await store.fetchAllDatabases()

    expect(apiClient.get).toHaveBeenCalledWith('/api/databases')
    expect(result).toHaveLength(2)
    expect(store.allDatabases).toHaveLength(2)
  })

  it('fetchAllDatabases stores databases with null title', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce([{ id: 'db-x', title: null }])

    const store = useDatabaseStore()
    await store.fetchAllDatabases()

    expect(store.allDatabases[0].title).toBeNull()
  })

  it('allDatabases starts as empty array', () => {
    const store = useDatabaseStore()
    expect(store.allDatabases).toEqual([])
  })

  it('fetchAllDatabases overwrites previous result on refetch', async () => {
    const store = useDatabaseStore()
    vi.mocked(apiClient.get).mockResolvedValueOnce([{ id: 'db-1', title: 'A' }])
    await store.fetchAllDatabases()
    expect(store.allDatabases).toHaveLength(1)

    vi.mocked(apiClient.get).mockResolvedValueOnce([
      { id: 'db-1', title: 'A' },
      { id: 'db-2', title: 'B' },
    ])
    await store.fetchAllDatabases()
    expect(store.allDatabases).toHaveLength(2)
  })
})

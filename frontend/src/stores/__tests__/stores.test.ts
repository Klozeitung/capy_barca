import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useBlockStore } from '@/stores/blocks'
import { useUiStore } from '@/stores/ui'
import { useDrag } from '@/composables/useDrag'
import { WS_BLOCK_EVENT, WS_RESYNC_EVENT, useWsStore } from '@/stores/ws'
import {
  useDatabaseStore,
  entriesEventDetails,
  eventTouchesEntry,
  OVERWRITE_NOTICE_WINDOW_MS,
  type PropertySchema,
} from '@/stores/database'
import { useAuthStore } from '@/stores/auth'
import { useDatabaseTemplatesStore } from '@/stores/databaseTemplates'
import { useNoticesStore } from '@/stores/notices'
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

  it('shares one request between concurrent fetchChildren calls', async () => {
    let resolve: (value: unknown) => void = () => {}
    vi.mocked(apiClient.get).mockReturnValueOnce(new Promise((r) => { resolve = r }))
    const store = useBlockStore()

    const first = store.fetchChildren('parent-id')
    const second = store.fetchChildren('parent-id')
    resolve([{ id: 'c1', parent_id: 'parent-id', type: 'paragraph', position: 1.0, state: 'active' }])
    await Promise.all([first, second])

    expect(apiClient.get).toHaveBeenCalledOnce()
    expect(store.childrenMap['parent-id']).toEqual(['c1'])
    expect(store.loadingChildren['parent-id']).toBe(false)
  })

  it('lets only the latest children request write the cache', async () => {
    let resolveOld: (value: unknown) => void = () => {}
    vi.mocked(apiClient.get)
      .mockReturnValueOnce(new Promise((r) => { resolveOld = r }))
      .mockResolvedValueOnce([{ id: 'new', parent_id: 'parent-id', type: 'paragraph', position: 1.0, state: 'active' }])
    const store = useBlockStore()

    const older = store.fetchChildren('parent-id')
    await store.fetchChildren('parent-id', true)
    resolveOld([{ id: 'old', parent_id: 'parent-id', type: 'paragraph', position: 1.0, state: 'active' }])
    await older

    expect(store.childrenMap['parent-id']).toEqual(['new'])
  })

  it('fetchTree caches every loaded parent and the mentioned blocks', async () => {
    const block = (id: string, parent: string, type = 'paragraph', position = 1.0) => ({
      id, parent_id: parent, type, position, state: 'active',
      reference_id: null, content: null, icon: null, cover: null,
    })
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      blocks: [
        block('layout', 'page', 'layout', 1.0),
        block('text', 'page', 'paragraph', 2.0),
        block('col-a', 'layout', 'column', 1.0),
        block('col-b', 'layout', 'column', 2.0),
        block('inner', 'col-a'),
      ],
      loaded: ['page', 'layout', 'col-a', 'col-b'],
      mentioned: [block('other', 'root', 'page')],
    })
    const store = useBlockStore()

    const children = await store.fetchTree('page')

    expect(apiClient.get).toHaveBeenCalledWith('/api/blocks/page/tree')
    expect(children.map((b) => b.id)).toEqual(['layout', 'text'])
    expect(store.childrenMap['layout']).toEqual(['col-a', 'col-b'])
    expect(store.childrenMap['col-a']).toEqual(['inner'])
    expect(store.childrenMap['col-b']).toEqual([])
    expect(store.hasLoadedChildren('inner')).toBe(false)
    expect(store.blocks['other']).toBeDefined()
    expect(store.loadingChildren['page']).toBe(false)
  })

  it('fetchChildren waits for a running tree load instead of asking itself', async () => {
    let resolve: (value: unknown) => void = () => {}
    vi.mocked(apiClient.get).mockReturnValueOnce(new Promise((r) => { resolve = r }))
    const store = useBlockStore()

    const tree = store.fetchTree('page')
    const sameTree = store.fetchTree('page')
    const children = store.fetchChildren('layout')
    resolve({
      blocks: [
        { id: 'layout', parent_id: 'page', type: 'layout', position: 1.0, state: 'active' },
        { id: 'col', parent_id: 'layout', type: 'column', position: 1.0, state: 'active' },
      ],
      loaded: ['page', 'layout', 'col'],
      mentioned: [],
    })
    await Promise.all([tree, sameTree])

    expect((await children).map((b) => b.id)).toEqual(['col'])
    expect(apiClient.get).toHaveBeenCalledOnce()
  })

  it('fetchTree uses the cache unless forced', async () => {
    const store = useBlockStore()
    store.childrenMap['page'] = []

    await store.fetchTree('page')
    expect(apiClient.get).not.toHaveBeenCalled()

    vi.mocked(apiClient.get).mockResolvedValueOnce({ blocks: [], loaded: ['page'], mentioned: [] })
    await store.fetchTree('page', true)
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

  it('a connection that opens again after a drop dispatches a resync', () => {
    const resyncs = vi.fn()
    window.addEventListener(WS_RESYNC_EVENT, resyncs)
    const store = useWsStore()
    store.connect()
    MockWs.instance!._open()
    expect(resyncs).not.toHaveBeenCalled()

    MockWs.instance!._closeWithCode(1006)
    vi.runAllTimers()
    MockWs.instance!._open()

    expect(resyncs).toHaveBeenCalledOnce()
    window.removeEventListener(WS_RESYNC_EVENT, resyncs)
  })

  it('a new connection after disconnect() does not resync', () => {
    const resyncs = vi.fn()
    window.addEventListener(WS_RESYNC_EVENT, resyncs)
    const store = useWsStore()
    store.connect()
    MockWs.instance!._open()
    store.disconnect()
    store.connect()
    MockWs.instance!._open()

    expect(resyncs).not.toHaveBeenCalled()
    window.removeEventListener(WS_RESYNC_EVENT, resyncs)
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

  // ── Database settings ───────────────────────────────────────────────────────

  it('fetchDatabaseSettings reads the settings of the database', async () => {
    const settings = { default_entry_icon: 'mdi:star-outline', can_manage: false }
    vi.mocked(apiClient.get).mockResolvedValueOnce(settings)

    const store = useDatabaseStore()
    await expect(store.fetchDatabaseSettings('db-1')).resolves.toEqual(settings)
    expect(apiClient.get).toHaveBeenCalledWith('/api/databases/db-1/settings')
  })

  it('setDefaultEntryIcon puts the icon, and null to remove it', async () => {
    vi.mocked(apiClient.put).mockResolvedValue({ default_entry_icon: null, can_manage: true })

    const store = useDatabaseStore()
    await store.setDefaultEntryIcon('db-1', 'mdi:star-outline')
    await store.setDefaultEntryIcon('db-1', null)

    expect(apiClient.put).toHaveBeenNthCalledWith(
      1, '/api/databases/db-1/settings', { default_entry_icon: 'mdi:star-outline' },
    )
    expect(apiClient.put).toHaveBeenNthCalledWith(
      2, '/api/databases/db-1/settings', { default_entry_icon: null },
    )
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

  it('updateSchema also re-fetches entries when they are cached', async () => {
    const updated = makeSchema({ name: 'Priority' })
    vi.mocked(apiClient.patch).mockResolvedValueOnce(updated)
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce([updated]) // fetchSchemas
      .mockResolvedValueOnce([])        // fetchEntries

    const store = useDatabaseStore()
    store.entries['db-1'] = [makeEntry()]

    await store.updateSchema('db-1', 'schema-1', { config: { hasTimeline: true } })

    // A config change can rewrite entry values server-side, so entries are
    // re-fetched in addition to schemas.
    expect(apiClient.get).toHaveBeenCalledTimes(2)
  })

  it('updateSchema does not re-fetch entries when not cached', async () => {
    const updated = makeSchema({ name: 'Priority' })
    vi.mocked(apiClient.patch).mockResolvedValueOnce(updated)
    vi.mocked(apiClient.get).mockResolvedValueOnce([updated]) // only fetchSchemas

    const store = useDatabaseStore()

    await store.updateSchema('db-1', 'schema-1', { config: { hasTimeline: true } })

    expect(apiClient.get).toHaveBeenCalledTimes(1)
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

  it('createEntry posts to correct endpoint without loading the database', async () => {
    const created = makeEntry({ id: 'entry-new', values: {} })
    vi.mocked(apiClient.post).mockResolvedValueOnce(created)

    const store = useDatabaseStore()
    const result = await store.createEntry('db-1')

    expect(apiClient.post).toHaveBeenCalledWith('/api/databases/db-1/entries')
    expect(result.id).toBe('entry-new')
    expect(apiClient.get).not.toHaveBeenCalled()
  })

  it('duplicateEntry posts to correct endpoint without loading the database', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce(makeEntry({ id: 'copy' }))

    const store = useDatabaseStore()
    const result = await store.duplicateEntry('db-1', 'entry-1')

    expect(apiClient.post).toHaveBeenCalledWith('/api/databases/db-1/entries/entry-1/duplicate')
    expect(result.id).toBe('copy')
    expect(apiClient.get).not.toHaveBeenCalled()
  })

  // ── upsertValue ─────────────────────────────────────────────────────────────

  it('upsertValue calls correct endpoint', async () => {
    vi.mocked(apiClient.put).mockResolvedValueOnce({ value: { text: 'Hello' } })

    const store = useDatabaseStore()
    await store.upsertValue('db-1', 'entry-1', 'schema-1', { text: 'Hello' })

    expect(apiClient.put).toHaveBeenCalledWith(
      '/api/databases/db-1/entries/entry-1/values/schema-1',
      { value: { text: 'Hello' } },
    )
  })

  it('upsertValue optimistically patches local entry', async () => {
    vi.mocked(apiClient.put).mockResolvedValueOnce({ value: { text: 'World' } })

    const store = useDatabaseStore()
    store.entries['db-1'] = [makeEntry({ id: 'entry-1', values: {} })]

    await store.upsertValue('db-1', 'entry-1', 'schema-1', { text: 'World' })

    const row = store.getEntries('db-1').find((e) => e.id === 'entry-1')
    expect(row?.values['schema-1']).toEqual({ text: 'World' })
  })

  it('upsertValue accepts null to clear a cell', async () => {
    vi.mocked(apiClient.put).mockResolvedValueOnce({ value: null })

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

  // ── resolveEntryTitles ───────────────────────────────────────────────────────

  it('resolveEntryTitles posts missing ids and caches descriptors', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce([
      { id: 'e1', title: 'Person A', database_id: 'db-1' },
      { id: 'e2', title: null, database_id: 'db-1' },
    ])

    const store = useDatabaseStore()
    await store.resolveEntryTitles('db-1', ['e1', 'e2'])

    expect(apiClient.post).toHaveBeenCalledWith(
      '/api/databases/db-1/entries/resolve-titles',
      { ids: ['e1', 'e2'] },
    )
    expect(store.getRelationTitle('e1')).toBe('Person A')
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

  // ── ensureSchemas ────────────────────────────────────────────────────────────

  it('ensureSchemas fetches once and serves the cache afterwards', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce([makeSchema()])

    const store = useDatabaseStore()
    await store.ensureSchemas('db-1')
    await store.ensureSchemas('db-1')

    expect(apiClient.get).toHaveBeenCalledTimes(1)
    expect(store.getSchemas('db-1')).toHaveLength(1)
  })

  it('ensureSchemas shares one request across concurrent callers', async () => {
    // Every keyed cell of a column asks on first render; they must not each
    // issue their own request for the same target database.
    vi.mocked(apiClient.get).mockResolvedValueOnce([makeSchema()])

    const store = useDatabaseStore()
    await Promise.all([
      store.ensureSchemas('db-1'),
      store.ensureSchemas('db-1'),
      store.ensureSchemas('db-1'),
    ])

    expect(apiClient.get).toHaveBeenCalledTimes(1)
  })

  // ── listKeyReferences ────────────────────────────────────────────────────────

  it('listKeyReferences returns the relations keyed on a property', async () => {
    const references = [
      {
        schema_id: 'schema-b',
        schema_name: 'Property B',
        database_id: 'db-a',
        database_title: 'Database A',
      },
    ]
    vi.mocked(apiClient.get).mockResolvedValueOnce(references)

    const store = useDatabaseStore()
    const result = await store.listKeyReferences('db-b', 'schema-date')

    expect(apiClient.get).toHaveBeenCalledWith(
      '/api/databases/db-b/schemas/schema-date/key-references',
    )
    expect(result).toEqual(references)
  })

  it('listKeyReferences yields an empty list on failure', async () => {
    // The backend clears the references either way, so a transient error must
    // not block the delete behind an unanswerable dialog.
    vi.mocked(apiClient.get).mockRejectedValueOnce(new Error('offline'))

    const store = useDatabaseStore()
    expect(await store.listKeyReferences('db-1', 'schema-1')).toEqual([])
  })

  // ── resolveEntryKeyValues ────────────────────────────────────────────────────

  it('resolveEntryKeyValues posts the key property and caches the values', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce([
      { id: 'e1', title: 'Entry A', database_id: 'db-1', value: { start: '2045-07-17' } },
    ])

    const store = useDatabaseStore()
    await store.resolveEntryKeyValues('db-1', ['e1'], 'schema-date')

    expect(apiClient.post).toHaveBeenCalledWith(
      '/api/databases/db-1/entries/resolve-titles',
      { ids: ['e1'], value_schema_id: 'schema-date' },
    )
    expect(store.getRelationKeyValue('schema-date', 'e1')).toEqual({ start: '2045-07-17' })
  })

  it('resolveEntryKeyValues is a no-op without ids or a key property', async () => {
    const store = useDatabaseStore()
    await store.resolveEntryKeyValues('db-1', [], 'schema-date')
    await store.resolveEntryKeyValues('db-1', ['e1'], '')
    expect(apiClient.post).not.toHaveBeenCalled()
  })

  it('resolveEntryKeyValues skips ids already resolved for that key property', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce([
      { id: 'e1', title: 'A', database_id: 'db-1', value: { number: 1 } },
    ])

    const store = useDatabaseStore()
    await store.resolveEntryKeyValues('db-1', ['e1'], 'schema-rank')
    await store.resolveEntryKeyValues('db-1', ['e1'], 'schema-rank')

    expect(apiClient.post).toHaveBeenCalledTimes(1)
  })

  it('resolveEntryKeyValues caches per key property, not per entry', async () => {
    // Two relations may key the same entry on different properties; the second
    // must not be served the first one's value.
    vi.mocked(apiClient.post)
      .mockResolvedValueOnce([
        { id: 'e1', title: 'A', database_id: 'db-1', value: { start: '2045-07-17' } },
      ])
      .mockResolvedValueOnce([
        { id: 'e1', title: 'A', database_id: 'db-1', value: { number: 3 } },
      ])

    const store = useDatabaseStore()
    await store.resolveEntryKeyValues('db-1', ['e1'], 'schema-date')
    await store.resolveEntryKeyValues('db-1', ['e1'], 'schema-rank')

    expect(apiClient.post).toHaveBeenCalledTimes(2)
    expect(store.getRelationKeyValue('schema-date', 'e1')).toEqual({ start: '2045-07-17' })
    expect(store.getRelationKeyValue('schema-rank', 'e1')).toEqual({ number: 3 })
  })

  it('resolveEntryKeyValues does not share the title cache dedupe', async () => {
    // resolveEntryTitles skips ids whose titles are known. If key values rode
    // along on that marker, a keyed cell would get no values for exactly the
    // entries it has already rendered.
    vi.mocked(apiClient.post)
      .mockResolvedValueOnce([{ id: 'e1', title: 'A', database_id: 'db-1' }])
      .mockResolvedValueOnce([
        { id: 'e1', title: 'A', database_id: 'db-1', value: { start: '2045-07-17' } },
      ])

    const store = useDatabaseStore()
    await store.resolveEntryTitles('db-1', ['e1'])
    await store.resolveEntryKeyValues('db-1', ['e1'], 'schema-date')

    expect(apiClient.post).toHaveBeenCalledTimes(2)
    expect(store.getRelationKeyValue('schema-date', 'e1')).toEqual({ start: '2045-07-17' })
  })

  it('resolveEntryKeyValues records a null value for an entry the server omits', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce([])

    const store = useDatabaseStore()
    await store.resolveEntryKeyValues('db-1', ['gone'], 'schema-date')

    expect(store.getRelationKeyValue('schema-date', 'gone')).toBeNull()
  })

  it('resolveEntryKeyValues records null when the entry stores no value', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce([
      { id: 'e1', title: 'A', database_id: 'db-1', value: null },
    ])

    const store = useDatabaseStore()
    await store.resolveEntryKeyValues('db-1', ['e1'], 'schema-date')

    expect(store.getRelationKeyValue('schema-date', 'e1')).toBeNull()
  })

  it('resolveEntryKeyValues retries after a failed request', async () => {
    vi.mocked(apiClient.post)
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce([
        { id: 'e1', title: 'A', database_id: 'db-1', value: { number: 5 } },
      ])

    const store = useDatabaseStore()
    await store.resolveEntryKeyValues('db-1', ['e1'], 'schema-rank')
    expect(store.getRelationKeyValue('schema-rank', 'e1')).toBeNull()

    await store.resolveEntryKeyValues('db-1', ['e1'], 'schema-rank')
    expect(store.getRelationKeyValue('schema-rank', 'e1')).toEqual({ number: 5 })
  })

  it('resolveEntryKeyValues refetches known ids when forced', async () => {
    vi.mocked(apiClient.post)
      .mockResolvedValueOnce([
        { id: 'e1', title: 'A', database_id: 'db-1', value: { start: '2045-07-17' } },
      ])
      .mockResolvedValueOnce([
        { id: 'e1', title: 'A', database_id: 'db-1', value: { start: '2046-01-01' } },
      ])

    const store = useDatabaseStore()
    await store.resolveEntryKeyValues('db-1', ['e1'], 'schema-date')
    await store.resolveEntryKeyValues('db-1', ['e1'], 'schema-date', true)

    expect(store.getRelationKeyValue('schema-date', 'e1')).toEqual({ start: '2046-01-01' })
  })

  it('getRelationKeyValue returns null for an unknown pair', () => {
    const store = useDatabaseStore()
    expect(store.getRelationKeyValue('schema-date', 'nobody')).toBeNull()
  })
})

// ── Database Store: view results, search, live updates ───────────────────────

function page(entries: ReturnType<typeof makeEntry>[], total = entries.length, extra = {}) {
  return { entries, total, offset: 0, relations: {}, key_values: {}, ...extra }
}

const FIRST_PAGE = { offset: 0, limit: 100, search: '' }

describe('useDatabaseStore view pages', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('loadViewPage asks the view endpoint for the requested range', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce(page([makeEntry()]))

    const store = useDatabaseStore()
    await store.loadViewPage('db-1', 'view-1', { offset: 100, limit: 50, search: 'abc' }, 'k')

    expect(apiClient.post).toHaveBeenCalledWith(
      '/api/databases/db-1/views/view-1/entries',
      { offset: 100, limit: 50, search: 'abc' },
    )
  })

  it('loadViewPage does not write the full entry cache', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce(page([makeEntry()], 500))

    const store = useDatabaseStore()
    await store.loadViewPage('db-1', 'view-1', FIRST_PAGE, 'view-a')

    // A page of a database must never stand in for the whole of it.
    expect(store.entries['db-1']).toBeUndefined()
  })

  it('loadViewPage keeps the result under its key and returns that stored list', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce(page([makeEntry()], 1))

    const store = useDatabaseStore()
    const result = await store.loadViewPage('db-1', 'view-1', FIRST_PAGE, 'view-a')

    expect(store.queryResults['view-a'].databaseId).toBe('db-1')
    expect(result.entries).toBe(store.queryResults['view-a'].entries)
    expect(result.total).toBe(1)
  })

  it('appending adds the next batch below and skips entries already shown', async () => {
    vi.mocked(apiClient.post)
      .mockResolvedValueOnce(page([makeEntry({ id: 'a' }), makeEntry({ id: 'b' })], 4))
      .mockResolvedValueOnce(page([makeEntry({ id: 'b' }), makeEntry({ id: 'c' })], 4))

    const store = useDatabaseStore()
    await store.loadViewPage('db-1', 'view-1', FIRST_PAGE, 'view-a')
    const result = await store.loadViewPage(
      'db-1', 'view-1', { offset: 2, limit: 2, search: '' }, 'view-a', true,
    )

    expect(result.entries.map((e) => e.id)).toEqual(['a', 'b', 'c'])
    expect(result.total).toBe(4)
  })

  it('without append the loaded range is replaced', async () => {
    vi.mocked(apiClient.post)
      .mockResolvedValueOnce(page([makeEntry({ id: 'a' })]))
      .mockResolvedValueOnce(page([makeEntry({ id: 'z' })]))

    const store = useDatabaseStore()
    await store.loadViewPage('db-1', 'view-1', FIRST_PAGE, 'view-a')
    await store.loadViewPage('db-1', 'view-1', FIRST_PAGE, 'view-a')

    expect(store.queryResults['view-a'].entries.map((e) => e.id)).toEqual(['z'])
  })

  it('a late response to an older request does not replace a newer result', async () => {
    let resolveOld: (v: unknown) => void = () => {}
    vi.mocked(apiClient.post)
      .mockImplementationOnce(() => new Promise((r) => { resolveOld = r }))
      .mockResolvedValueOnce(page([makeEntry({ id: 'new' })]))

    const store = useDatabaseStore()
    const older = store.loadViewPage('db-1', 'view-1', FIRST_PAGE, 'view-a')
    await store.loadViewPage('db-1', 'view-1', FIRST_PAGE, 'view-a')
    resolveOld(page([makeEntry({ id: 'old' })]))
    await older

    expect(store.queryResults['view-a'].entries.map((e) => e.id)).toEqual(['new'])
  })

  it('releaseQueryResult drops the keyed result', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce(page([makeEntry()]))

    const store = useDatabaseStore()
    await store.loadViewPage('db-1', 'view-1', FIRST_PAGE, 'view-a')
    store.releaseQueryResult('view-a')

    expect(store.queryResults['view-a']).toBeUndefined()
  })

  it('getEntries falls back to the rows open views have loaded', async () => {
    vi.mocked(apiClient.post)
      .mockResolvedValueOnce(page([makeEntry({ id: 'a' }), makeEntry({ id: 'b' })]))
      .mockResolvedValueOnce(page([makeEntry({ id: 'b' }), makeEntry({ id: 'c' })]))

    const store = useDatabaseStore()
    await store.loadViewPage('db-1', 'view-1', FIRST_PAGE, 'view-a')
    await store.loadViewPage('db-1', 'view-2', FIRST_PAGE, 'view-b')

    expect(store.getEntries('db-1').map((e) => e.id)).toEqual(['a', 'b', 'c'])
    expect(store.getEntries('db-2')).toEqual([])
  })

  it('getEntries prefers the full list once it is loaded', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce(page([makeEntry({ id: 'a' })]))
    vi.mocked(apiClient.get).mockResolvedValueOnce([
      makeEntry({ id: 'a' }), makeEntry({ id: 'z' }),
    ])

    const store = useDatabaseStore()
    await store.loadViewPage('db-1', 'view-1', FIRST_PAGE, 'view-a')
    await store.fetchEntries('db-1')

    expect(store.getEntries('db-1').map((e) => e.id)).toEqual(['a', 'z'])
  })

  it('upsertValue patches the rows of every view of that database', async () => {
    vi.mocked(apiClient.post)
      .mockResolvedValueOnce(page([makeEntry({ id: 'e1', values: {} })]))
      .mockResolvedValueOnce(page([makeEntry({ id: 'e1', values: {} })]))
    vi.mocked(apiClient.put).mockResolvedValueOnce({ value: { text: 'Hi' } })

    const store = useDatabaseStore()
    await store.loadViewPage('db-1', 'view-1', FIRST_PAGE, 'view-a')
    await store.loadViewPage('db-2', 'view-2', FIRST_PAGE, 'view-other-db')
    await store.upsertValue('db-1', 'e1', 'schema-1', { text: 'Hi' })

    expect(store.queryResults['view-a'].entries[0].values['schema-1']).toEqual({ text: 'Hi' })
    // Same entry id in another database is a different entry.
    expect(store.queryResults['view-other-db'].entries[0].values['schema-1']).toBeUndefined()
  })
})

describe('useDatabaseStore relation resolution from pages', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  function storeWithRelation(config: Record<string, unknown> = {}) {
    const store = useDatabaseStore()
    const schema = (id: string, type: string, cfg: Record<string, unknown>): PropertySchema => ({
      id, database_id: 'db-1', name: id, type, config: cfg, position: 1, group: 'Standard',
    })
    store.schemas['db-1'] = [
      schema('link', 'relation', { target_database_id: 'db-2', ...config }),
      schema('text', 'text', {}),
    ]
    return store
  }

  it('resolved ids get their titles and count as resolved', async () => {
    const store = storeWithRelation()
    vi.mocked(apiClient.post).mockResolvedValueOnce(page(
      [makeEntry({ id: 'e1', values: { link: { related_ids: ['t1'] } } })],
      1,
      { relations: { t1: { id: 't1', title: 'Entry A', database_id: 'db-2' } } },
    ))

    await store.loadViewPage('db-1', 'view-1', FIRST_PAGE, 'k')

    expect(store.getRelationTitle('t1')).toBe('Entry A')
    expect(store.isRelationResolved('t1')).toBe(true)
  })

  it('ids missing from the page resolution count as gone', async () => {
    const store = storeWithRelation()
    store.relationTitles['t2'] = { id: 't2', title: 'Old', database_id: 'db-2' }
    vi.mocked(apiClient.post).mockResolvedValueOnce(page(
      [makeEntry({ id: 'e1', values: { link: { related_ids: ['t2'] } } })],
    ))

    await store.loadViewPage('db-1', 'view-1', FIRST_PAGE, 'k')

    expect(store.isRelationResolved('t2')).toBe(true)
    expect(store.hasRelationEntry('t2')).toBe(false)
  })

  it('a relation cell finds everything resolved and does not ask again', async () => {
    const store = storeWithRelation()
    vi.mocked(apiClient.post).mockResolvedValueOnce(page(
      [makeEntry({ id: 'e1', values: { link: { related_ids: ['t1'] } } })],
      1,
      { relations: { t1: { id: 't1', title: 'Entry A', database_id: 'db-2' } } },
    ))
    await store.loadViewPage('db-1', 'view-1', FIRST_PAGE, 'k')
    vi.mocked(apiClient.post).mockClear()

    await store.resolveEntryTitles('db-2', ['t1'])

    expect(apiClient.post).not.toHaveBeenCalled()
  })

  it('timeline periods are all taken into account', async () => {
    const store = storeWithRelation()
    vi.mocked(apiClient.post).mockResolvedValueOnce(page(
      [makeEntry({ id: 'e1', values: { link: { _timeline: {
        '2000-01-01→2000-12-31': { related_ids: ['t1'] },
        '2001-01-01→': { related_ids: ['t2'] },
      } } } })],
    ))

    await store.loadViewPage('db-1', 'view-1', FIRST_PAGE, 'k')

    expect(store.isRelationResolved('t1')).toBe(true)
    expect(store.isRelationResolved('t2')).toBe(true)
  })

  it('keyed relations get their key values, null where none is stored', async () => {
    const store = storeWithRelation({ keying: { enabled: true, key_property_id: 'when' } })
    vi.mocked(apiClient.post).mockResolvedValueOnce(page(
      [makeEntry({ id: 'e1', values: { link: { related_ids: ['t1', 't2'] } } })],
      1,
      {
        relations: {
          t1: { id: 't1', title: 'Entry A', database_id: 'db-2' },
          t2: { id: 't2', title: 'Entry B', database_id: 'db-2' },
        },
        key_values: { when: { t1: { start: '2000-07-28' } } },
      },
    ))

    await store.loadViewPage('db-1', 'view-1', FIRST_PAGE, 'k')
    vi.mocked(apiClient.post).mockClear()
    await store.resolveEntryKeyValues('db-2', ['t1', 't2'], 'when')

    expect(store.getRelationKeyValue('when', 't1')).toEqual({ start: '2000-07-28' })
    expect(store.getRelationKeyValue('when', 't2')).toBeNull()
    expect(apiClient.post).not.toHaveBeenCalled()
  })
})

describe('useDatabaseStore single entries', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('fetchEntry loads one entry and getEntry finds it', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      entry: makeEntry({ id: 'e1' }), relations: {}, key_values: {},
    })

    const store = useDatabaseStore()
    const entry = await store.fetchEntry('db-1', 'e1')

    expect(apiClient.get).toHaveBeenCalledWith('/api/databases/db-1/entries/e1')
    expect(store.getEntry('db-1', 'e1')).toBe(entry)
    expect(store.getEntry('db-2', 'e1')).toBeNull()
    expect(store.entries['db-1']).toBeUndefined()
  })

  it('upsertValue reaches a single entry', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      entry: makeEntry({ id: 'e1', values: {} }), relations: {}, key_values: {},
    })
    vi.mocked(apiClient.put).mockResolvedValueOnce({ value: { text: 'Hi' } })

    const store = useDatabaseStore()
    await store.fetchEntry('db-1', 'e1')
    await store.upsertValue('db-1', 'e1', 'schema-1', { text: 'Hi' })

    expect(store.getEntry('db-1', 'e1')?.values['schema-1']).toEqual({ text: 'Hi' })
  })

  it('getEntry falls back to a view that shows the entry', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce(page([makeEntry({ id: 'e1' })]))

    const store = useDatabaseStore()
    await store.loadViewPage('db-1', 'view-1', FIRST_PAGE, 'k')

    expect(store.getEntry('db-1', 'e1')?.id).toBe('e1')
  })
})

describe('useDatabaseStore.searchEntries', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('searches titles on the server, ordered by title, limited', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({ entries: [], total: 0 })

    const store = useDatabaseStore()
    await store.searchEntries('db-1', '  per ', 20)

    expect(apiClient.post).toHaveBeenCalledWith(
      '/api/databases/db-1/entries/query',
      expect.objectContaining({
        filter_groups: [{
          conjunction: 'and',
          filters: [expect.objectContaining({ schema_id: '__name__', operator: 'contains', value: 'per' })],
        }],
        sorts: [{ schema_id: '__name__', direction: 'asc' }],
        limit: 20,
      }),
    )
  })

  it('an empty query lists entries without a filter and never matches exactly', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({ entries: [makeEntry()], total: 1 })

    const store = useDatabaseStore()
    const result = await store.searchEntries('db-1', '   ')

    expect(vi.mocked(apiClient.post).mock.calls[0][1]).toMatchObject({ filter_groups: [] })
    expect(result.exactMatch).toBe(false)
    expect(apiClient.post).toHaveBeenCalledTimes(1)
  })

  it('answers exactMatch from the page when the page holds every match', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      entries: [makeEntry({ content: { title: 'Person A' } })],
      total: 1,
    })

    const store = useDatabaseStore()
    const result = await store.searchEntries('db-1', 'person a')

    expect(result.exactMatch).toBe(true)
    expect(apiClient.post).toHaveBeenCalledTimes(1)
  })

  it('asks the server for an exact title when matches exceed the page', async () => {
    // The exact title sits past the first page: it must still be found, or
    // the picker would offer to create a duplicate.
    vi.mocked(apiClient.post)
      .mockResolvedValueOnce({
        entries: [makeEntry({ content: { title: 'Person AB' } })],
        total: 120,
      })
      .mockResolvedValueOnce({ entries: [makeEntry()], total: 1 })

    const store = useDatabaseStore()
    const result = await store.searchEntries('db-1', 'Person A', 1)

    expect(result.exactMatch).toBe(true)
    expect(vi.mocked(apiClient.post).mock.calls[1][1]).toMatchObject({
      filter_groups: [{ filters: [expect.objectContaining({ operator: 'eq', value: 'Person A' })] }],
      limit: 1,
    })
  })

  it('reports no exact match when neither page nor server has one', async () => {
    vi.mocked(apiClient.post)
      .mockResolvedValueOnce({ entries: [makeEntry({ content: { title: 'Person AB' } })], total: 120 })
      .mockResolvedValueOnce({ entries: [], total: 0 })

    const store = useDatabaseStore()
    const result = await store.searchEntries('db-1', 'Person A', 1)

    expect(result.exactMatch).toBe(false)
  })

  it('does not cache search results', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({ entries: [makeEntry()], total: 1 })

    const store = useDatabaseStore()
    await store.searchEntries('db-1', 'x')

    expect(store.entries['db-1']).toBeUndefined()
    expect(Object.keys(store.queryResults)).toEqual([])
  })
})

describe('useDatabaseStore._applyEntryEvent', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  async function storeWithChip() {
    vi.mocked(apiClient.post).mockResolvedValueOnce([
      { id: 'e1', title: 'Old', database_id: 'db-target' },
    ])
    const store = useDatabaseStore()
    await store.resolveEntryTitles('db-target', ['e1'])
    return store
  }

  it('content_updated renames the relation chip', async () => {
    const store = await storeWithChip()

    store._applyEntryEvent(makePayload('content_updated', {
      block_id: 'e1',
      after: makeBlock({ id: 'e1', content: { title: 'New' } }),
    }))

    expect(store.getRelationTitle('e1')).toBe('New')
  })

  it('content_updated renames the row in views and in the full list', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce(page([makeEntry({ id: 'e1' })]))
    const store = useDatabaseStore()
    await store.loadViewPage('db-1', 'view-1', FIRST_PAGE, 'view-a')
    store.entries['db-1'] = [makeEntry({ id: 'e1' })]

    store._applyEntryEvent(makePayload('content_updated', {
      block_id: 'e1',
      after: makeBlock({ id: 'e1', content: { title: 'Renamed' } }),
    }))

    expect(store.queryResults['view-a'].entries[0].content?.title).toBe('Renamed')
    expect(store.entries['db-1'][0].content?.title).toBe('Renamed')
  })

  it('reverted is applied like content_updated', async () => {
    const store = await storeWithChip()

    store._applyEntryEvent(makePayload('reverted', {
      block_id: 'e1',
      after: makeBlock({ id: 'e1', content: { title: 'Back' } }),
    }))

    expect(store.getRelationTitle('e1')).toBe('Back')
  })

  it('a title removed by the edit becomes an untitled chip, not a stale one', async () => {
    const store = await storeWithChip()

    store._applyEntryEvent(makePayload('content_updated', {
      block_id: 'e1',
      after: makeBlock({ id: 'e1', content: {} }),
    }))

    expect(store.hasRelationEntry('e1')).toBe(true)
    expect(store.getRelationTitle('e1')).toBeNull()
  })

  it('trashing the target drops its chip, restoring resolves it again', async () => {
    const store = await storeWithChip()

    store._applyEntryEvent(makePayload('state_changed', {
      block_id: 'e1', before: { state: 'active' }, after: { state: 'trash' },
    }))
    expect(store.hasRelationEntry('e1')).toBe(false)

    vi.mocked(apiClient.post).mockResolvedValueOnce([
      { id: 'e1', title: 'Old', database_id: 'db-target' },
    ])
    store._applyEntryEvent(makePayload('state_changed', {
      block_id: 'e1', before: { state: 'trash' }, after: { state: 'active' },
    }))
    await vi.waitFor(() => expect(store.hasRelationEntry('e1')).toBe(true))
    expect(apiClient.post).toHaveBeenLastCalledWith(
      '/api/databases/db-target/entries/resolve-titles',
      { ids: ['e1'] },
    )
  })

  it('purged drops the chip', async () => {
    const store = await storeWithChip()

    store._applyEntryEvent(makePayload('purged', { block_id: 'e1' }))

    expect(store.hasRelationEntry('e1')).toBe(false)
  })

  it('reacts to WebSocket block events dispatched on window', async () => {
    const store = await storeWithChip()

    window.dispatchEvent(new CustomEvent(WS_BLOCK_EVENT, {
      detail: makePayload('content_updated', {
        block_id: 'e1',
        after: makeBlock({ id: 'e1', content: { title: 'Via socket' } }),
      }),
    }))

    expect(store.getRelationTitle('e1')).toBe('Via socket')
  })

  it('ignores events for blocks it does not know', () => {
    const store = useDatabaseStore()
    store._applyEntryEvent(makePayload('content_updated', {
      block_id: 'unknown',
      after: makeBlock({ id: 'unknown', content: { title: 'X' } }),
    }))
    expect(store.hasRelationEntry('unknown')).toBe(false)
  })
})

describe('useDatabaseStore.getNameLabel', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('returns the custom label of the database block', () => {
    const blocks = useBlockStore()
    blocks.blocks['db-1'] = makeBlock({ id: 'db-1', type: 'database', content: { nameLabel: ' Date ' } })
    expect(useDatabaseStore().getNameLabel('db-1')).toBe('Date')
  })

  it('returns null without a label, for a blank one, or for an unknown block', () => {
    const blocks = useBlockStore()
    blocks.blocks['db-1'] = makeBlock({ id: 'db-1', type: 'database', content: { title: 'T' } })
    blocks.blocks['db-2'] = makeBlock({ id: 'db-2', type: 'database', content: { nameLabel: '  ' } })
    const store = useDatabaseStore()
    expect(store.getNameLabel('db-1')).toBeNull()
    expect(store.getNameLabel('db-2')).toBeNull()
    expect(store.getNameLabel('nope')).toBeNull()
  })
})

// ── Option ids ───────────────────────────────────────────────────────────────

import {
  normalizeSelectOption,
  schemaOptions,
  nuanceOptions,
  findOption,
  optionLabel,
} from '@/stores/database'

describe('option helpers', () => {
  const schema = {
    config: {
      options: [{ id: 'o1', label: 'Value A', color: 'red' }, { id: 'o2', label: 'Value B' }],
      nuance: { enabled: true, options: [{ id: 'n1', label: 'Nuance A' }] },
    },
  }

  it('normalizes stored options and keeps their ids', () => {
    expect(normalizeSelectOption({ id: 'o1', label: 'Value A', color: 'red' }))
      .toEqual({ id: 'o1', label: 'Value A', color: 'red' })
    // A config the backend has not stored yet falls back to the label.
    expect(normalizeSelectOption('Value A')).toEqual({ id: 'Value A', label: 'Value A' })
    expect(normalizeSelectOption({ label: 'Value B' })).toEqual({ id: 'Value B', label: 'Value B' })
  })

  it('reads select and nuance options of a property', () => {
    expect(schemaOptions(schema).map((o) => o.id)).toEqual(['o1', 'o2'])
    expect(nuanceOptions(schema).map((o) => o.label)).toEqual(['Nuance A'])
    expect(schemaOptions({ config: null })).toEqual([])
    expect(nuanceOptions(null)).toEqual([])
  })

  it('resolves ids to options and labels', () => {
    const options = schemaOptions(schema)
    expect(findOption(options, 'o2')?.label).toBe('Value B')
    expect(findOption(options, 'gone')).toBeUndefined()
    expect(optionLabel(options, 'o1')).toBe('Value A')
    expect(optionLabel(options, undefined)).toBe('')
  })
})

describe('useDatabaseStore.optionUsage', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('asks the usage endpoint and returns the entry count', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ entries: 4 })
    const store = useDatabaseStore()

    await expect(store.optionUsage('db-1', 'schema-1', 'o1')).resolves.toBe(4)
    expect(apiClient.get).toHaveBeenCalledWith(
      '/api/databases/db-1/schemas/schema-1/options/o1/usage',
    )
  })
})

// ── Changed entries ───────────────────────────────────────────────────────────

function entriesEvent(after: Record<string, unknown>): BlockEventPayload {
  return {
    event_id: null,
    event_type: 'database_entries_updated',
    block_id: after.database_id as string,
    before: null,
    after,
    created_at: '2000-07-28T00:00:00Z',
  }
}

function dispatch(payload: BlockEventPayload): void {
  window.dispatchEvent(new CustomEvent(WS_BLOCK_EVENT, { detail: payload }))
}

describe('entries event helpers', () => {
  it('reads the details of an entries event only', () => {
    const payload = entriesEvent({ database_id: 'db-1', entries: { a: ['s'] } })
    expect(entriesEventDetails(payload)?.entries).toEqual({ a: ['s'] })
    expect(entriesEventDetails({ ...payload, event_type: 'content_updated' })).toBeNull()
    expect(entriesEventDetails({ ...payload, after: null })).toBeNull()
  })

  it('an event concerns an entry it lists, or any entry when it lists none', () => {
    expect(eventTouchesEntry({ database_id: 'db-1', entries: { a: [] } }, 'a')).toBe(true)
    expect(eventTouchesEntry({ database_id: 'db-1', entries: { a: [] } }, 'b')).toBe(false)
    expect(eventTouchesEntry({ database_id: 'db-1' }, 'b')).toBe(true)
    expect(eventTouchesEntry({ database_id: 'db-1', entries: {}, listing: true }, 'b')).toBe(true)
  })
})

describe('useDatabaseStore.refreshEntries', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('loads the entries in one request and updates every cached copy', async () => {
    const store = useDatabaseStore()
    vi.mocked(apiClient.post).mockResolvedValueOnce(page([makeEntry({ id: 'a' }), makeEntry({ id: 'b' })]))
    await store.loadViewPage('db-1', 'view-1', FIRST_PAGE, 'view-a')
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      entries: [makeEntry({ id: 'a', content: { title: 'Row B' }, values: { s1: { text: 'Value A' } } })],
      missing: ['b'],
      relations: {},
      key_values: {},
    })

    const missing = await store.refreshEntries('db-1', ['a', 'b', 'a'])

    expect(apiClient.post).toHaveBeenLastCalledWith(
      '/api/databases/db-1/entries/batch', { ids: ['a', 'b'] },
    )
    const row = store.queryResults['view-a'].entries[0]
    expect(row.values).toEqual({ s1: { text: 'Value A' } })
    expect(row.content).toEqual({ title: 'Row B' })
    expect(missing).toEqual(['b'])
  })

  it('views asking for the same entries at once share the request', async () => {
    const store = useDatabaseStore()
    let resolve: (value: unknown) => void = () => {}
    vi.mocked(apiClient.post).mockReturnValueOnce(new Promise((r) => { resolve = r }))

    const first = store.refreshEntries('db-1', ['b', 'a'])
    const second = store.refreshEntries('db-1', ['a', 'b'])
    resolve({ entries: [], missing: [], relations: {}, key_values: {} })
    await Promise.all([first, second])

    expect(apiClient.post).toHaveBeenCalledOnce()
  })
})

describe('notice when someone else overwrites a value', () => {
  const schema: PropertySchema = {
    id: 's1', database_id: 'db-1', name: 'Property A', type: 'text', position: 1, config: null,
  } as PropertySchema

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.useFakeTimers()
    vi.mocked(apiClient.put).mockResolvedValue({ value: { text: 'Value A' } })
    useAuthStore().login('person-a', 'member')
    const store = useDatabaseStore()
    store.schemas['db-1'] = [schema]
    store.entryDetails['e1'] = { databaseId: 'db-1', entry: makeEntry({ id: 'e1', content: { title: 'Entry A' } }) }
  })

  afterEach(() => {
    // The store listens on window; a disposed store stops answering events
    // meant for the next test.
    useDatabaseStore().$dispose()
    vi.useRealTimers()
  })

  function overwrite(actor: string, cell: [string, string] = ['e1', 's1']): void {
    dispatch(entriesEvent({
      database_id: 'db-1',
      entries: { [cell[0]]: [cell[1]] },
      actor: { id: 'u2', name: actor },
    }))
  }

  it('reports a change by someone else to a value written here a moment ago', async () => {
    await useDatabaseStore().upsertValue('db-1', 'e1', 's1', { text: 'Value A' })

    overwrite('person-b')

    expect(useNoticesStore().notices).toHaveLength(1)
    expect(useNoticesStore().notices[0]).toMatchObject({
      key: 'notices.valueOverwritten',
      params: { user: 'person-b', property: 'Property A', entry: 'Entry A' },
    })
  })

  it('reports a change to the cell being edited', () => {
    useDatabaseStore().setEditingCell('view-a', 'e1', 's1')
    overwrite('person-b')
    expect(useNoticesStore().notices).toHaveLength(1)
  })

  it('stays quiet about own changes, other cells and old writes', async () => {
    const store = useDatabaseStore()
    await store.upsertValue('db-1', 'e1', 's1', { text: 'Value A' })

    overwrite('person-a')
    overwrite('person-b', ['e1', 's2'])
    vi.advanceTimersByTime(OVERWRITE_NOTICE_WINDOW_MS + 1)
    overwrite('person-b')

    expect(useNoticesStore().notices).toHaveLength(0)
  })

  it('stays quiet once the cell is no longer edited', () => {
    const store = useDatabaseStore()
    store.setEditingCell('view-a', 'e1', 's1')
    store.setEditingCell('view-a', null, null)
    overwrite('person-b')
    expect(useNoticesStore().notices).toHaveLength(0)
  })
})

// ── Pending writes ────────────────────────────────────────────────────────────

function deferred<T = unknown>() {
  let resolve: (value: T) => void = () => {}
  let reject: (reason: unknown) => void = () => {}
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

describe('useDatabaseStore pending writes', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    // Answers queued but not used by an earlier test must not leak into these.
    vi.mocked(apiClient.put).mockReset()
    vi.mocked(apiClient.post).mockReset()
    vi.mocked(apiClient.get).mockReset()
  })

  afterEach(() => {
    useDatabaseStore().$dispose()
  })

  async function storeWithRow(values: Record<string, unknown> = { s1: { text: 'Old' } }) {
    vi.mocked(apiClient.post).mockResolvedValueOnce(page([makeEntry({ id: 'e1', values })]))
    const store = useDatabaseStore()
    await store.loadViewPage('db-1', 'view-1', FIRST_PAGE, 'view-a')
    return store
  }

  const row = (store: ReturnType<typeof useDatabaseStore>) => store.queryResults['view-a'].entries[0]

  it('a cell is pending until the server answers', async () => {
    const store = await storeWithRow()
    const put = deferred()
    vi.mocked(apiClient.put).mockReturnValueOnce(put.promise)

    const write = store.upsertValue('db-1', 'e1', 's1', { text: 'New' })
    expect(store.isCellPending('e1', 's1')).toBe(true)
    expect(store.isCellPending('e1', 's2')).toBe(false)
    expect(store.hasPendingWrites).toBe(true)

    put.resolve({ value: { text: 'New' } })
    await write
    expect(store.isCellPending('e1', 's1')).toBe(false)
    expect(store.hasPendingWrites).toBe(false)
  })

  it('a second write to the same cell is sent only after the first was answered', async () => {
    const store = await storeWithRow()
    const first = deferred()
    vi.mocked(apiClient.put).mockReturnValueOnce(first.promise).mockResolvedValueOnce({ value: { text: 'Two' } })

    const a = store.upsertValue('db-1', 'e1', 's1', { text: 'One' })
    const b = store.upsertValue('db-1', 'e1', 's1', { text: 'Two' })
    await vi.waitFor(() => expect(apiClient.put).toHaveBeenCalled())
    await new Promise((r) => setTimeout(r, 0))
    expect(apiClient.put).toHaveBeenCalledTimes(1)

    first.resolve({ value: { text: 'One' } })
    await Promise.all([a, b])
    expect(vi.mocked(apiClient.put).mock.calls.map((c) => c[1])).toEqual([
      { value: { text: 'One' } },
      { value: { text: 'Two' } },
    ])
    expect(store.isCellPending('e1', 's1')).toBe(false)
    expect(row(store).values['s1']).toEqual({ text: 'Two' })
  })

  it('writes to different cells do not wait for each other', async () => {
    const store = await storeWithRow()
    const first = deferred()
    vi.mocked(apiClient.put).mockReturnValueOnce(first.promise).mockResolvedValueOnce({ value: { text: 'Two' } })

    store.upsertValue('db-1', 'e1', 's1', { text: 'One' })
    await store.upsertValue('db-1', 'e1', 's2', { text: 'Two' })

    expect(apiClient.put).toHaveBeenCalledTimes(2)
    expect(store.isCellPending('e1', 's1')).toBe(true)
    expect(store.isCellPending('e1', 's2')).toBe(false)
    first.resolve({ value: { text: 'One' } })
  })

  it('a page loaded while the cell is pending keeps the new value', async () => {
    const store = await storeWithRow()
    const put = deferred()
    vi.mocked(apiClient.put).mockReturnValueOnce(put.promise)
    vi.mocked(apiClient.post).mockResolvedValueOnce(page([
      makeEntry({ id: 'e1', values: { s1: { text: 'Old' }, s2: { text: 'Server' } } }),
    ]))

    const write = store.upsertValue('db-1', 'e1', 's1', { text: 'New' })
    await store.loadViewPage('db-1', 'view-1', FIRST_PAGE, 'view-a')

    expect(row(store).values['s1']).toEqual({ text: 'New' })
    // Cells this client did not write take what the server sent.
    expect(row(store).values['s2']).toEqual({ text: 'Server' })
    put.resolve({ value: { text: 'New' } })
    await write
  })

  it('a page that started before the confirmation keeps the new value', async () => {
    const store = await storeWithRow()
    const put = deferred()
    const load = deferred()
    vi.mocked(apiClient.put).mockReturnValueOnce(put.promise)
    vi.mocked(apiClient.post).mockReturnValueOnce(load.promise)

    const write = store.upsertValue('db-1', 'e1', 's1', { text: 'New' })
    const reload = store.loadViewPage('db-1', 'view-1', FIRST_PAGE, 'view-a')
    put.resolve({ value: { text: 'New' } })
    await write
    // The server read this page before the write was committed.
    load.resolve(page([makeEntry({ id: 'e1', values: { s1: { text: 'Old' } } })]))
    await reload

    expect(row(store).values['s1']).toEqual({ text: 'New' })
  })

  it('a page that started after the confirmation shows what the server holds', async () => {
    const store = await storeWithRow()
    vi.mocked(apiClient.put).mockResolvedValueOnce({ value: { text: 'New' } })
    await store.upsertValue('db-1', 'e1', 's1', { text: 'New' })

    // Someone else changed the value after this client's write.
    vi.mocked(apiClient.post).mockResolvedValueOnce(page([
      makeEntry({ id: 'e1', values: { s1: { text: 'Later' } } }),
    ]))
    await store.loadViewPage('db-1', 'view-1', FIRST_PAGE, 'view-a')

    expect(row(store).values['s1']).toEqual({ text: 'Later' })
  })

  it('refreshed entries keep a pending value', async () => {
    const store = await storeWithRow()
    const put = deferred()
    vi.mocked(apiClient.put).mockReturnValueOnce(put.promise)
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      entries: [makeEntry({ id: 'e1', values: { s1: { text: 'Old' }, s2: { text: 'Server' } } })],
      missing: [], relations: {}, key_values: {},
    })

    const write = store.upsertValue('db-1', 'e1', 's1', { text: 'New' })
    await store.refreshEntries('db-1', ['e1'])

    expect(row(store).values).toEqual({ s1: { text: 'New' }, s2: { text: 'Server' } })
    put.resolve({ value: { text: 'New' } })
    await write
  })

  it('a single entry loaded while the cell is pending keeps the new value', async () => {
    const store = useDatabaseStore()
    const put = deferred()
    vi.mocked(apiClient.put).mockReturnValueOnce(put.promise)
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      entry: makeEntry({ id: 'e1', values: { s1: { text: 'Old' } } }), relations: {}, key_values: {},
    })

    const write = store.upsertValue('db-1', 'e1', 's1', { text: 'New' })
    const entry = await store.fetchEntry('db-1', 'e1')

    expect(entry.values['s1']).toEqual({ text: 'New' })
    put.resolve({ value: { text: 'New' } })
    await write
  })

  it('the full entry list loaded while the cell is pending keeps the new value', async () => {
    const store = useDatabaseStore()
    const put = deferred()
    vi.mocked(apiClient.put).mockReturnValueOnce(put.promise)
    vi.mocked(apiClient.get).mockResolvedValueOnce([makeEntry({ id: 'e1', values: { s1: { text: 'Old' } } })])

    const write = store.upsertValue('db-1', 'e1', 's1', { text: 'New' })
    await store.fetchEntries('db-1')

    expect(store.entries['db-1'][0].values['s1']).toEqual({ text: 'New' })
    put.resolve({ value: { text: 'New' } })
    await write
  })

  it('a failed write reloads the entry and passes the error on', async () => {
    const store = await storeWithRow()
    vi.mocked(apiClient.put).mockRejectedValueOnce(new Error('refused'))
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      entries: [makeEntry({ id: 'e1', values: { s1: { text: 'Old' } } })],
      missing: [], relations: {}, key_values: {},
    })

    await expect(store.upsertValue('db-1', 'e1', 's1', { text: 'New' })).rejects.toThrow('refused')

    expect(store.isCellPending('e1', 's1')).toBe(false)
    expect(apiClient.post).toHaveBeenLastCalledWith('/api/databases/db-1/entries/batch', { ids: ['e1'] })
    await vi.waitFor(() => expect(row(store).values['s1']).toEqual({ text: 'Old' }))
  })

  it('a failed write does not stop the next write to the same cell', async () => {
    const store = await storeWithRow()
    const first = deferred()
    vi.mocked(apiClient.put).mockReturnValueOnce(first.promise).mockResolvedValueOnce({ value: { text: 'Two' } })
    vi.mocked(apiClient.post).mockResolvedValue({
      entries: [], missing: [], relations: {}, key_values: {},
    })

    const a = store.upsertValue('db-1', 'e1', 's1', { text: 'One' })
    const b = store.upsertValue('db-1', 'e1', 's1', { text: 'Two' })
    first.reject(new Error('refused'))

    await expect(a).rejects.toThrow('refused')
    await b
    expect(apiClient.put).toHaveBeenLastCalledWith(
      '/api/databases/db-1/entries/e1/values/s1', { value: { text: 'Two' } },
    )
    expect(store.isCellPending('e1', 's1')).toBe(false)
  })

  // ── Stored value from the answer ─────────────────────────────────────────

  // A timeline relation is sent as its pool; the server adds the _timeline
  // the cell renders from.
  const POOL = { relationPool: { t1: [''] } }
  const STORED_POOL = { ...POOL, _timeline: { '': { related_ids: ['t1'] } } }

  it('the stored value replaces the value sent in every cached copy', async () => {
    const store = await storeWithRow()
    store.entries['db-1'] = [makeEntry({ id: 'e1', values: {} })]
    store.entryDetails['e1'] = { databaseId: 'db-1', entry: makeEntry({ id: 'e1', values: {} }) }
    vi.mocked(apiClient.put).mockResolvedValueOnce({ value: STORED_POOL })

    await store.upsertValue('db-1', 'e1', 's1', POOL)

    expect(row(store).values['s1']).toEqual(STORED_POOL)
    expect(store.entries['db-1'][0].values['s1']).toEqual(STORED_POOL)
    expect(store.getEntry('db-1', 'e1')?.values['s1']).toEqual(STORED_POOL)
  })

  it('a page that started before the confirmation shows the stored value', async () => {
    // The change event arrives before the answer to the write, so the reload
    // it causes is still running when the write is confirmed.
    const store = await storeWithRow()
    const put = deferred()
    const load = deferred()
    vi.mocked(apiClient.put).mockReturnValueOnce(put.promise)
    vi.mocked(apiClient.post).mockReturnValueOnce(load.promise)

    const write = store.upsertValue('db-1', 'e1', 's1', POOL)
    const reload = store.loadViewPage('db-1', 'view-1', FIRST_PAGE, 'view-a')
    put.resolve({ value: STORED_POOL })
    await write
    load.resolve(page([makeEntry({ id: 'e1', values: { s1: { text: 'Old' } } })]))
    await reload

    expect(row(store).values['s1']).toEqual(STORED_POOL)
  })

  it('only the answer to the last write to a cell replaces its value', async () => {
    const store = await storeWithRow()
    const first = deferred()
    const second = deferred()
    vi.mocked(apiClient.put).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)

    const a = store.upsertValue('db-1', 'e1', 's1', { text: 'One' })
    const b = store.upsertValue('db-1', 'e1', 's1', { text: 'Two' })
    first.resolve({ value: { text: 'One (stored)' } })
    await a
    await vi.waitFor(() => expect(apiClient.put).toHaveBeenCalledTimes(2))

    // The later input stays while its own write is still on the way.
    expect(row(store).values['s1']).toEqual({ text: 'Two' })
    expect(store.isCellPending('e1', 's1')).toBe(true)

    second.resolve({ value: { text: 'Two (stored)' } })
    await b
    expect(row(store).values['s1']).toEqual({ text: 'Two (stored)' })
    expect(store.isCellPending('e1', 's1')).toBe(false)
  })

  it('the stored value reaches the entry template of that id', async () => {
    const templates = useDatabaseTemplatesStore()
    templates.templates['db-1'] = [{ id: 'tmpl-1', position: 1, content: null, icon: null, values: {} }]
    vi.mocked(apiClient.put).mockResolvedValueOnce({ value: STORED_POOL })

    const write = useDatabaseStore().upsertValue('db-1', 'tmpl-1', 's1', POOL)
    expect(templates.templates['db-1'][0].values['s1']).toEqual(POOL)
    await write

    expect(templates.templates['db-1'][0].values['s1']).toEqual(STORED_POOL)
  })

  it('leaving the page asks first while a write is pending', async () => {
    const store = useDatabaseStore()
    const idle = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(idle)
    expect(idle.defaultPrevented).toBe(false)

    const put = deferred()
    vi.mocked(apiClient.put).mockReturnValueOnce(put.promise)
    const write = store.upsertValue('db-1', 'e1', 's1', { text: 'New' })
    const busy = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(busy)
    expect(busy.defaultPrevented).toBe(true)

    put.resolve({ value: { text: 'New' } })
    await write
  })
})

import { ref, onScopeDispose } from 'vue'
import { defineStore } from 'pinia'
import { apiClient } from '@/api/client'
import { WS_BLOCK_EVENT, type BlockEventPayload } from '@/stores/ws'

// ── Domain types ──────────────────────────────────────────────────────────────

export interface Block {
  id: string
  parent_id: string | null
  reference_id: string | null
  type: string
  position: number
  state: 'active' | 'trash'
  content: Record<string, unknown> | null
  icon: string | null
  cover: string | null
  owner_id?: string | null
}

export interface BlockPreference {
  block_id: string
  key: string
  value: unknown
}

/**
 * Response of GET /api/blocks/{id}/tree.
 *
 * ``loaded`` names every parent whose children are all in ``blocks``; a
 * parent listed there without children in ``blocks`` has none.
 * ``mentioned`` holds the blocks that inline mentions in the content refer to.
 */
export interface BlockTree {
  blocks: Block[]
  loaded: string[]
  mentioned: Block[]
}

export interface DeleteResult {
  affected: string[]
}

export interface RestoreResult {
  restored: string[]
}

export interface RebalanceResult {
  rebalanced: string[]
}

// ── Store ─────────────────────────────────────────────────────────────────────

/**
 * Central block store.
 *
 * Maintains a flat map of block data keyed by block ID, and a separate
 * children map keyed by parent ID. A page loads its whole content at once
 * through ``fetchTree``; any container it did not cover loads its children
 * lazily on first access. Both are cached until explicitly invalidated.
 *
 * _storeBlock patches existing objects in-place (Object.assign) rather than
 * replacing them. This keeps all references to the same block object valid
 * so that components which received the object as a prop automatically see
 * the updated fields without a full tree re-render.
 *
 * WebSocket integration
 * ---------------------
 * The store registers a listener for the `capybarca:block-event` DOM custom
 * event (dispatched by the ws store on every incoming server push) and
 * applies the event to local state via `_applyEvent`. This makes all open
 * views reactive to mutations performed in other browser tabs or future
 * sessions without any polling.
 *
 * Own mutations (REST calls that already applied the change) are safe to
 * receive again via WS: `_storeBlock` is idempotent for identical data, and
 * childrenMap invalidation is harmless when the cache is already gone.
 */
export const useBlockStore = defineStore('blocks', () => {
  const blocks = ref<Record<string, Block>>({})
  const childrenMap = ref<Record<string, string[]>>({})
  const loadingChildren = ref<Record<string, boolean>>({})

  /**
   * Children requests in flight, by parent ID. Components that mount at the
   * same time and ask for the same children share one request.
   */
  const _childrenRequests = new Map<string, Promise<Block[]>>()

  /**
   * Tree requests in flight, by root ID. While one runs, ``fetchChildren``
   * waits for it before asking for children on its own, because the tree
   * most likely holds them already.
   */
  const _treeRequests = new Map<string, Promise<Block[]>>()

  /**
   * Preferences keyed by blockId then by key.
   */
  const preferences = ref<Record<string, Record<string, unknown>>>({})

  // ── Internal helpers ──────────────────────────────────────────────────────

  function _storeBlock(block: Block): void {
    if (blocks.value[block.id]) {
      // Patch in place so all existing references to the same object
      // (e.g. props passed down to child components) pick up the changes.
      Object.assign(blocks.value[block.id], block)
    } else {
      blocks.value[block.id] = block
    }
  }

  function _storeChildren(parentId: string, children: Block[]): void {
    children.forEach(_storeBlock)
    childrenMap.value[parentId] = children.map((b) => b.id)
  }

  // ── WebSocket event application ───────────────────────────────────────────

  /**
   * Apply an inbound block event from the WebSocket to local state.
   *
   * Each event type is handled conservatively: only the fields present in
   * the payload are touched. Unknown event types are silently ignored so
   * that future backend additions do not require a coordinated frontend
   * deploy.
   */
  function _applyEvent(payload: BlockEventPayload): void {
    const { event_type, block_id, before, after } = payload

    switch (event_type) {
      case 'created': {
        if (!after || !block_id) break
        _storeBlock(after as Block)
        const parentId = (after as Block).parent_id
        if (parentId) delete childrenMap.value[parentId]
        break
      }

      case 'content_updated':
      case 'appearance_updated':
      case 'reverted': {
        if (!after || !block_id) break
        _storeBlock(after as Block)
        break
      }

      case 'moved': {
        if (!after || !block_id) break
        const oldParentId = (before as Block | null)?.parent_id ?? null
        const newParentId = (after as Block).parent_id ?? null
        _storeBlock(after as Block)
        if (oldParentId) delete childrenMap.value[oldParentId]
        if (newParentId && newParentId !== oldParentId) delete childrenMap.value[newParentId]
        break
      }

      case 'state_changed': {
        if (!block_id) break
        const newState = (after as { state?: string } | null)?.state
        if (newState && blocks.value[block_id]) {
          const parentId = blocks.value[block_id].parent_id
          blocks.value[block_id].state = newState as Block['state']
          if (parentId) delete childrenMap.value[parentId]
        }
        break
      }

      case 'purged': {
        if (!block_id) break
        const parentId = blocks.value[block_id]?.parent_id ?? null
        delete blocks.value[block_id]
        if (parentId) delete childrenMap.value[parentId]
        break
      }

      default:
        break
    }
  }

  // Register the global DOM event listener for the lifetime of this store
  // instance. onScopeDispose fires when the Pinia store is torn down (e.g.
  // during tests), so there are no memory leaks.
  function _onWsBlockEvent(e: Event): void {
    _applyEvent((e as CustomEvent<BlockEventPayload>).detail)
  }

  window.addEventListener(WS_BLOCK_EVENT, _onWsBlockEvent)
  onScopeDispose(() => {
    window.removeEventListener(WS_BLOCK_EVENT, _onWsBlockEvent)
  })

  // ── Block fetching ────────────────────────────────────────────────────────

  async function fetchBlock(blockId: string): Promise<Block> {
    const block = await apiClient.get<Block>(`/api/blocks/${blockId}`)
    _storeBlock(block)
    return blocks.value[block.id]
  }

  /**
   * Load the direct children of *parentId*.
   *
   * Served from the cache when it holds them, unless *force* is set. A call
   * while a tree is loading waits for the tree first, and a call while a
   * request for the same parent is running waits for that request instead
   * of starting another. A forced call always starts a new one, and
   * only the latest request per parent writes its result to the cache, so a
   * slower, older response cannot overwrite a fresher one.
   */
  async function fetchChildren(parentId: string, force = false): Promise<Block[]> {
    if (!force) {
      if (childrenMap.value[parentId] === undefined && _treeRequests.size > 0) {
        await Promise.allSettled(_treeRequests.values())
      }
      if (childrenMap.value[parentId] !== undefined) {
        return childrenMap.value[parentId]
          .map((id) => blocks.value[id])
          .filter(Boolean)
      }
      const pending = _childrenRequests.get(parentId)
      if (pending) return pending
    }
    loadingChildren.value[parentId] = true
    const request: Promise<Block[]> = apiClient
      .get<Block[]>(`/api/blocks/${parentId}/children`)
      .then((children) => {
        if (_childrenRequests.get(parentId) === request) _storeChildren(parentId, children)
        return children
      })
      .finally(() => {
        if (_childrenRequests.get(parentId) === request) {
          _childrenRequests.delete(parentId)
          loadingChildren.value[parentId] = false
        }
      })
    _childrenRequests.set(parentId, request)
    return request
  }

  /**
   * Load everything the page *rootId* shows in one request: its children
   * and, at any depth, those of its layouts, columns, toggles and synced
   * blocks, plus the blocks its inline mentions refer to.
   *
   * Every parent the response covers is cached as loaded, including those
   * without children, so no component asks for them again. Served from the
   * cache when the children of *rootId* are already there, unless *force*
   * is set; a call while the same tree is loading shares that request.
   * Returns the children of *rootId*.
   */
  async function fetchTree(rootId: string, force = false): Promise<Block[]> {
    if (!force) {
      if (childrenMap.value[rootId] !== undefined) return getChildren(rootId)
      const pending = _treeRequests.get(rootId)
      if (pending) return pending
    }
    loadingChildren.value[rootId] = true
    const request: Promise<Block[]> = apiClient
      .get<BlockTree>(`/api/blocks/${rootId}/tree`)
      .then((tree) => {
        tree.mentioned.forEach(_storeBlock)
        const byParent: Record<string, Block[]> = {}
        for (const parentId of tree.loaded) byParent[parentId] = []
        for (const block of tree.blocks) {
          if (block.parent_id && byParent[block.parent_id]) byParent[block.parent_id].push(block)
        }
        for (const [parentId, children] of Object.entries(byParent)) {
          _storeChildren(parentId, children)
        }
        return getChildren(rootId)
      })
      .finally(() => {
        if (_treeRequests.get(rootId) === request) {
          _treeRequests.delete(rootId)
          loadingChildren.value[rootId] = false
        }
      })
    _treeRequests.set(rootId, request)
    return request
  }

  function getChildren(parentId: string): Block[] {
    const ids = childrenMap.value[parentId]
    if (ids !== undefined) {
      return ids.map((id) => blocks.value[id]).filter(Boolean)
    }
    // Cache is stale (invalidated by a mutation, re-fetch in flight).
    // Derive children from the flat blocks map so the rendered list never
    // collapses to an empty array during the async gap, which would cause the
    // browser to clamp the scroll position to 0.
    // Deleted blocks are already marked state='trash' before the cache is
    // cleared, so they are correctly excluded by the consumer's active-filter.
    // Moved blocks already have their parent_id updated in _storeBlock before
    // the cache is cleared, so they appear in the right parent immediately.
    return Object.values(blocks.value)
      .filter((b) => b.parent_id === parentId)
      .sort((a, b) => a.position - b.position)
  }

  function hasLoadedChildren(parentId: string): boolean {
    return childrenMap.value[parentId] !== undefined
  }

  // ── Block mutations ────────────────────────────────────────────────────────

  async function createBlock(payload: {
    type: string
    parent_id: string
    position?: number
    reference_id?: string
    content?: Record<string, unknown>
    icon?: string
    cover?: string
  }): Promise<Block> {
    const block = await apiClient.post<Block>('/api/blocks', payload)
    _storeBlock(block)
    if (payload.parent_id) {
      delete childrenMap.value[payload.parent_id]
    }
    return blocks.value[block.id]
  }

  async function updateBlock(
    blockId: string,
    payload: {
      type?: string
      content?: Record<string, unknown>
      position?: number
      state?: string
    },
  ): Promise<Block> {
    const block = await apiClient.patch<Block>(`/api/blocks/${blockId}`, payload)
    _storeBlock(block)
    return blocks.value[block.id]
  }

  async function updateAppearance(
    blockId: string,
    payload: { icon?: string; cover?: string },
  ): Promise<Block> {
    const block = await apiClient.patch<Block>(`/api/blocks/${blockId}/appearance`, payload)
    _storeBlock(block)
    return blocks.value[block.id]
  }

  async function moveBlock(
    blockId: string,
    oldParentId: string | null,
    newParentId: string,
    newPosition: number,
  ): Promise<Block> {
    const block = await apiClient.post<Block>(`/api/blocks/${blockId}/move`, {
      new_parent_id: newParentId,
      new_position: newPosition,
    })
    _storeBlock(block)
    if (oldParentId) delete childrenMap.value[oldParentId]
    delete childrenMap.value[newParentId]
    return blocks.value[block.id]
  }

  async function deleteBlock(blockId: string, parentId: string | null): Promise<DeleteResult> {
    const data = await apiClient.delete<DeleteResult>(`/api/blocks/${blockId}`)
    data.affected.forEach((id) => {
      if (blocks.value[id]) blocks.value[id].state = 'trash'
    })
    if (parentId) delete childrenMap.value[parentId]
    return data
  }

  async function fetchTrashed(): Promise<Block[]> {
    const trashed = await apiClient.get<Block[]>('/api/blocks/trash')
    trashed.forEach(_storeBlock)
    return trashed
  }

  async function restoreBlock(blockId: string): Promise<RestoreResult> {
    const data = await apiClient.post<RestoreResult>(`/api/blocks/${blockId}/restore`)
    data.restored.forEach((id) => {
      if (blocks.value[id]) blocks.value[id].state = 'active'
    })
    const block = blocks.value[blockId]
    if (block?.parent_id) delete childrenMap.value[block.parent_id]
    return data
  }

  async function purgeBlock(blockId: string): Promise<void> {
    await apiClient.delete(`/api/blocks/${blockId}/purge`)
    const parentId = blocks.value[blockId]?.parent_id ?? null
    delete blocks.value[blockId]
    if (parentId) delete childrenMap.value[parentId]
  }

  async function deepDuplicateBlock(blockId: string, parentId: string): Promise<Block> {
    const block = await apiClient.post<Block>(`/api/blocks/${blockId}/duplicate`)
    _storeBlock(block)
    delete childrenMap.value[parentId]
    return blocks.value[block.id]
  }

  async function rebalanceChildren(blockId: string): Promise<RebalanceResult> {
    return apiClient.post<RebalanceResult>(`/api/blocks/${blockId}/rebalance-children`)
  }

  // ── Preferences ───────────────────────────────────────────────────────────

  async function fetchPreferences(blockId: string): Promise<void> {
    try {
      const prefs = await apiClient.get<BlockPreference[]>(`/api/blocks/${blockId}/preferences`)
      preferences.value[blockId] = preferences.value[blockId] ?? {}
      prefs.forEach((p) => {
        preferences.value[blockId][p.key] = p.value
      })
    } catch {
      // Preferences are non-critical; silently ignore fetch failures
    }
  }

  function getPreference<T = unknown>(blockId: string, key: string, defaultValue: T): T {
    return (preferences.value[blockId]?.[key] as T) ?? defaultValue
  }

  async function setPreference(blockId: string, key: string, value: unknown): Promise<void> {
    preferences.value[blockId] = preferences.value[blockId] ?? {}
    preferences.value[blockId][key] = value
    await apiClient.put(`/api/blocks/${blockId}/preferences/${key}`, { value })
  }

  async function toggleFolded(blockId: string): Promise<void> {
    const current = getPreference<boolean>(blockId, 'folded', false)
    await setPreference(blockId, 'folded', !current)
  }

  return {
    // State (exposed for direct reactive access in components/tests)
    blocks,
    childrenMap,
    loadingChildren,
    preferences,

    // Block operations
    fetchBlock,
    fetchChildren,
    fetchTree,
    getChildren,
    hasLoadedChildren,
    createBlock,
    updateBlock,
    updateAppearance,
    moveBlock,
    deleteBlock,
    deepDuplicateBlock,
    fetchTrashed,
    restoreBlock,
    purgeBlock,
    rebalanceChildren,

    // Preference operations
    fetchPreferences,
    getPreference,
    setPreference,
    toggleFolded,

    // Exposed for testing
    _applyEvent,
  }
})

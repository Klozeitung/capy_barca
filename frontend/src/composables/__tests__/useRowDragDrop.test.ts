import { describe, it, expect, vi, beforeEach } from 'vitest'
import { ref } from 'vue'
import { dropZoneAt, positionsAround, useRowDragDrop } from '@/composables/useRowDragDrop'
import type { DatabaseEntry, PropertySchema } from '@/stores/database'

const drag = vi.hoisted(() => ({
  startDrag: vi.fn(),
  endDrag: vi.fn(),
  getDragging: vi.fn(() => ({ blockId: 'b', sourceParentId: 'db-1', blockType: 'page', dragMode: 'block' })),
  dropBetween: vi.fn(async () => {}),
}))

vi.mock('@/composables/useDrag', () => ({ useDrag: () => drag }))

function entry(id: string, position: number): DatabaseEntry {
  return { id, position, content: null, icon: null, state: 'active', values: {} }
}

describe('dropZoneAt', () => {
  it('splits a row into above, onto and below', () => {
    expect(dropZoneAt(0.1)).toEqual({ above: true, below: false, onto: false })
    expect(dropZoneAt(0.5)).toEqual({ above: false, below: false, onto: true })
    expect(dropZoneAt(0.9)).toEqual({ above: false, below: true, onto: false })
  })
})

describe('positionsAround', () => {
  const rows = [entry('a', 1), entry('b', 2), entry('c', 3)]

  it('drops above between the previous row and the target', () => {
    expect(positionsAround(rows, rows[1], true)).toEqual([1, 2])
    expect(positionsAround(rows, rows[0], true)).toEqual([null, 1])
  })

  it('drops below between the target and the next row', () => {
    expect(positionsAround(rows, rows[1], false)).toEqual([2, 3])
    expect(positionsAround(rows, rows[2], false)).toEqual([3, null])
  })
})

describe('useRowDragDrop drops', () => {
  const rows = [entry('a', 1), entry('b', 2), entry('c', 3)]
  const parentItem = { id: 'parent', type: 'parent_item' } as PropertySchema

  beforeEach(() => {
    vi.clearAllMocks()
  })

  function setup(withParentItem: boolean) {
    const setParent = vi.fn(async () => {})
    const requery = vi.fn(async () => {})
    const rowDrag = useRowDragDrop({
      databaseId: 'db-1',
      editable: ref(true),
      parentItemSchema: ref(withParentItem ? parentItem : null),
      rows: ref(rows),
      setParent,
      requery,
    })
    return { rowDrag, setParent, requery }
  }

  /** Hover *target* at *relativeY* of its height, then drop there. */
  async function dropAt(rowDrag: ReturnType<typeof setup>['rowDrag'], target: DatabaseEntry, relativeY: number) {
    const event = {
      preventDefault: vi.fn(),
      stopPropagation: vi.fn(),
      clientY: relativeY * 20,
      dataTransfer: { dropEffect: '' },
      currentTarget: { getBoundingClientRect: () => ({ top: 0, height: 20 }) },
    } as unknown as DragEvent
    rowDrag.onRowDragOver(event, target)
    await rowDrag.onRowDrop(event, target)
  }

  it('a drop onto a row makes a sub-item and loads nothing', async () => {
    const { rowDrag, setParent, requery } = setup(true)

    await dropAt(rowDrag, rows[2], 0.5)

    expect(setParent).toHaveBeenCalledWith('b', 'c')
    expect(drag.dropBetween).not.toHaveBeenCalled()
    expect(requery).not.toHaveBeenCalled()
  })

  it('without the sub-item pair a drop onto a row reorders and updates the rows', async () => {
    const { rowDrag, setParent, requery } = setup(false)

    await dropAt(rowDrag, rows[0], 0.5)

    expect(setParent).not.toHaveBeenCalled()
    expect(drag.dropBetween).toHaveBeenCalledWith('db-1', 1, 2)
    expect(requery).toHaveBeenCalledOnce()
  })

  it('a drop above a row reorders and updates the rows', async () => {
    const { rowDrag, requery } = setup(true)

    await dropAt(rowDrag, rows[2], 0.1)

    expect(drag.dropBetween).toHaveBeenCalledWith('db-1', 2, 3)
    expect(requery).toHaveBeenCalledOnce()
  })
})

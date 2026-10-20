import { describe, it, expect } from 'vitest'
import { dropZoneAt, positionsAround } from '@/composables/useRowDragDrop'
import type { DatabaseEntry } from '@/stores/database'

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

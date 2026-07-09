import { describe, it, expect } from 'vitest'
import {
  schemaIdsInGroup,
  removeGroupFromOrder,
  removeGroupFromFolded,
  reorderGroups,
  hideSchemaInAllViews,
} from '../propertySectionHelpers'
import type { DatabaseView, PropertySchema } from '@/stores/database'

// ── Fixtures ──────────────────────────────────────────────────────────────────

const DEFAULT_GROUP = 'Standard'

function schema(id: string, group?: string): PropertySchema {
  return {
    id,
    database_id: 'db-1',
    name: id,
    type: 'text',
    config: {},
    position: 0,
    group: group as string,
  }
}

function view(id: string, hiddenColumns: string[] = []): DatabaseView {
  return {
    id,
    name: id,
    viewType: 'table',
    colOrder: ['__name__'],
    colWidths: {},
    filterGroups: [],
    sorts: [],
    hiddenColumns,
  }
}

// ── schemaIdsInGroup ──────────────────────────────────────────────────────────

describe('schemaIdsInGroup', () => {
  it('returns IDs whose explicit group matches', () => {
    const schemas = [schema('a', 'Meta'), schema('b', 'Meta'), schema('c', 'Other')]
    expect(schemaIdsInGroup(schemas, 'Meta', DEFAULT_GROUP)).toEqual(['a', 'b'])
  })

  it('treats absent / empty group as the default group', () => {
    const schemas = [schema('a'), schema('b', ''), schema('c', 'Meta')]
    expect(schemaIdsInGroup(schemas, DEFAULT_GROUP, DEFAULT_GROUP)).toEqual(['a', 'b'])
  })

  it('returns an empty array when no schema matches', () => {
    expect(schemaIdsInGroup([schema('a', 'Meta')], 'Ghost', DEFAULT_GROUP)).toEqual([])
  })
})

// ── removeGroupFromOrder ──────────────────────────────────────────────────────

describe('removeGroupFromOrder', () => {
  it('removes the group while preserving the rest of the order', () => {
    expect(removeGroupFromOrder(['A', 'B', 'C'], 'B')).toEqual(['A', 'C'])
  })

  it('is a no-op when the group is absent', () => {
    expect(removeGroupFromOrder(['A', 'C'], 'B')).toEqual(['A', 'C'])
  })

  it('does not mutate the input array', () => {
    const order = ['A', 'B']
    removeGroupFromOrder(order, 'A')
    expect(order).toEqual(['A', 'B'])
  })
})

// ── removeGroupFromFolded ─────────────────────────────────────────────────────

describe('removeGroupFromFolded', () => {
  it('removes the key and keeps the others', () => {
    expect(removeGroupFromFolded({ A: true, B: false }, 'A')).toEqual({ B: false })
  })

  it('returns a new object and does not mutate the input', () => {
    const folded = { A: true }
    const result = removeGroupFromFolded(folded, 'A')
    expect(result).not.toBe(folded)
    expect(folded).toEqual({ A: true })
  })
})

// ── reorderGroups ─────────────────────────────────────────────────────────────

describe('reorderGroups', () => {
  it('drops a forward-dragged group after the target', () => {
    // Dragging A onto C lands A on C's far side (the direction of travel).
    expect(reorderGroups(['A', 'B', 'C'], 'A', 'C')).toEqual(['B', 'C', 'A'])
  })

  it('drops a backward-dragged group before the target', () => {
    expect(reorderGroups(['A', 'B', 'C'], 'C', 'A')).toEqual(['C', 'A', 'B'])
  })

  it('swaps adjacent groups regardless of drag direction', () => {
    expect(reorderGroups(['A', 'B', 'C'], 'B', 'A')).toEqual(['B', 'A', 'C'])
    expect(reorderGroups(['A', 'B', 'C'], 'A', 'B')).toEqual(['B', 'A', 'C'])
  })

  it('is a no-op when source and target are the same', () => {
    expect(reorderGroups(['A', 'B', 'C'], 'B', 'B')).toEqual(['A', 'B', 'C'])
  })

  it('is a no-op when the source group is absent', () => {
    expect(reorderGroups(['A', 'B'], 'Ghost', 'A')).toEqual(['A', 'B'])
  })

  it('is a no-op when the target group is absent', () => {
    expect(reorderGroups(['A', 'B'], 'A', 'Ghost')).toEqual(['A', 'B'])
  })

  it('does not mutate the input array', () => {
    const order = ['A', 'B', 'C']
    reorderGroups(order, 'A', 'C')
    expect(order).toEqual(['A', 'B', 'C'])
  })

  it('always returns a new array reference', () => {
    const order = ['A', 'B']
    expect(reorderGroups(order, 'A', 'A')).not.toBe(order)
    expect(reorderGroups(order, 'Ghost', 'A')).not.toBe(order)
  })
})

// ── hideSchemaInAllViews ──────────────────────────────────────────────────────

describe('hideSchemaInAllViews', () => {
  it('adds the schema to every view that does not already hide it', () => {
    const views = [view('v1'), view('v2', ['x'])]
    const { views: out, changed } = hideSchemaInAllViews(views, 'new')
    expect(changed).toBe(true)
    expect(out[0].hiddenColumns).toEqual(['new'])
    expect(out[1].hiddenColumns).toEqual(['x', 'new'])
  })

  it('reports no change and returns the original ref when all views already hide it', () => {
    const views = [view('v1', ['new']), view('v2', ['new'])]
    const result = hideSchemaInAllViews(views, 'new')
    expect(result.changed).toBe(false)
    expect(result.views).toBe(views)
  })

  it('does not mutate the input views', () => {
    const views = [view('v1')]
    hideSchemaInAllViews(views, 'new')
    expect(views[0].hiddenColumns).toEqual([])
  })

  it('tolerates a missing hiddenColumns array', () => {
    const broken = { ...view('v1'), hiddenColumns: undefined } as unknown as DatabaseView
    const { views: out, changed } = hideSchemaInAllViews([broken], 'new')
    expect(changed).toBe(true)
    expect(out[0].hiddenColumns).toEqual(['new'])
  })
})

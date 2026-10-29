import { describe, it, expect } from 'vitest'
import {
  schemaIdsInGroup,
  removeGroupFromOrder,
  removeGroupFromFolded,
  reorderGroups,
  isPropertyValueEmpty,
  isSchemaVisible,
  orderSchemaGroups,
} from '../propertySectionHelpers'
import type { PropertySchema, DatabaseEntry } from '@/stores/database'

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

// ── isPropertyValueEmpty ──────────────────────────────────────────────────────

describe('isPropertyValueEmpty', () => {
  it.each([
    [undefined],
    [null],
    [{ value: '' }],
    [{ value: null }],
    [{ related_ids: [] }],
    [{ files: [] }],
    [{ checked: false }],
    [{ selected: '' }],
    [{ selected: [] }],
  ])('treats %j as empty', (val) => {
    expect(isPropertyValueEmpty(val as Record<string, unknown> | null | undefined)).toBe(true)
  })

  it.each([
    [{ value: 'Text A' }],
    [{ value: 0 }],
    [{ related_ids: ['e-1'] }],
    [{ checked: true }],
    [{ selected: 'opt-1' }],
    [{ start: '2000-07-28' }],
  ])('treats %j as filled', (val) => {
    expect(isPropertyValueEmpty(val)).toBe(false)
  })
})

// ── isSchemaVisible ───────────────────────────────────────────────────────────

function entry(values: DatabaseEntry['values']): DatabaseEntry {
  return { id: 'e-1', position: 0, content: null, icon: null, state: 'active', values }
}

describe('isSchemaVisible', () => {
  const filled = entry({ a: { value: 'Text A' } })
  const empty = entry({})

  it('shows by default', () => {
    expect(isSchemaVisible(schema('a'), undefined, empty)).toBe(true)
    expect(isSchemaVisible(schema('a'), 'show', empty)).toBe(true)
  })

  it('hides regardless of the value', () => {
    expect(isSchemaVisible(schema('a'), 'hide', filled)).toBe(false)
  })

  it('hides only empty values in hide_empty mode', () => {
    expect(isSchemaVisible(schema('a'), 'hide_empty', filled)).toBe(true)
    expect(isSchemaVisible(schema('a'), 'hide_empty', empty)).toBe(false)
  })
})

// ── orderSchemaGroups ─────────────────────────────────────────────────────────

function positioned(id: string, position: number, group?: string): PropertySchema {
  return { ...schema(id, group), position }
}

describe('orderSchemaGroups', () => {
  const all = [
    positioned('b', 2),
    positioned('a', 1),
    positioned('x', 1, 'Zeta'),
    positioned('y', 1, 'Beta'),
    positioned('z', 1, 'Alpha'),
  ]

  it('puts the default group first, then the stored order, then the rest alphabetically', () => {
    const groups = orderSchemaGroups(all, ['Zeta'], DEFAULT_GROUP, () => true)
    expect(groups.map(g => g.name)).toEqual([DEFAULT_GROUP, 'Zeta', 'Alpha', 'Beta'])
    expect(groups[0].isDefault).toBe(true)
    expect(groups.slice(1).every(g => !g.isDefault)).toBe(true)
  })

  it('orders properties by position within a group', () => {
    const [standard] = orderSchemaGroups(all, [], DEFAULT_GROUP, () => true)
    expect(standard.schemas.map(s => s.id)).toEqual(['a', 'b'])
  })

  it('leaves out invisible properties but keeps their group', () => {
    const groups = orderSchemaGroups(all, [], DEFAULT_GROUP, s => s.id !== 'z')
    expect(groups.find(g => g.name === 'Alpha')?.schemas).toEqual([])
  })

  it('keeps stored groups that hold no property anymore', () => {
    const groups = orderSchemaGroups([], ['Ghost'], DEFAULT_GROUP, () => true)
    expect(groups).toEqual([{ name: 'Ghost', isDefault: false, schemas: [] }])
  })

  it('does not reorder the input array', () => {
    const input = [positioned('b', 2), positioned('a', 1)]
    orderSchemaGroups(input, [], DEFAULT_GROUP, () => true)
    expect(input.map(s => s.id)).toEqual(['b', 'a'])
  })
})

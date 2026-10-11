import { describe, it, expect } from 'vitest'
import { orderAndSplitColumns, type OrderableColumn } from './viewSettingsHelpers'

function col(key: string, name = key, isName = false): OrderableColumn {
  return { key, name, isName }
}

const NAME = '__name__'

describe('orderAndSplitColumns', () => {
  it('keeps the name column first when it is shown', () => {
    const cols = [col('z', 'Zeta'), col(NAME, 'Name', true), col('a', 'Alpha')]
    const { visible, hidden } = orderAndSplitColumns(cols, new Set())
    expect(visible[0].key).toBe(NAME)
    expect(hidden.some((c) => c.key === NAME)).toBe(false)
  })

  it('moves a hidden name column to the front of the hidden group', () => {
    const cols = [col('z', 'Zeta'), col(NAME, 'Name', true), col('a', 'Alpha')]
    const { visible, hidden } = orderAndSplitColumns(cols, new Set([NAME, 'z']))
    expect(visible.map((c) => c.key)).toEqual(['a'])
    expect(hidden.map((c) => c.key)).toEqual([NAME, 'z'])
  })

  it('pins the name column regardless of its label', () => {
    // A renamed lead property still leads; it is not sorted alphabetically.
    const cols = [col('a', 'Alpha'), col(NAME, 'Zulu date', true)]
    const { visible } = orderAndSplitColumns(cols, new Set())
    expect(visible.map((c) => c.key)).toEqual([NAME, 'a'])
  })

  it('sorts visible non-name columns alphabetically by name', () => {
    const cols = [
      col(NAME, 'Name', true),
      col('c', 'Charlie'),
      col('a', 'Alpha'),
      col('b', 'Bravo'),
    ]
    const { visible } = orderAndSplitColumns(cols, new Set())
    expect(visible.map((c) => c.key)).toEqual([NAME, 'a', 'b', 'c'])
  })

  it('sorts case-insensitively', () => {
    const cols = [col(NAME, 'Name', true), col('b', 'banana'), col('a', 'Apple')]
    const { visible } = orderAndSplitColumns(cols, new Set())
    expect(visible.map((c) => c.key)).toEqual([NAME, 'a', 'b'])
  })

  it('uses natural numeric ordering', () => {
    const cols = [
      col(NAME, 'Name', true),
      col('x', 'Item 10'),
      col('y', 'Item 2'),
    ]
    const { visible } = orderAndSplitColumns(cols, new Set())
    expect(visible.map((c) => c.key)).toEqual([NAME, 'y', 'x'])
  })

  it('sorts both the visible and the hidden group alphabetically', () => {
    const cols = [
      col(NAME, 'Name', true),
      col('d', 'Delta'),
      col('a', 'Alpha'),
      col('c', 'Charlie'),
      col('b', 'Bravo'),
    ]
    const { visible, hidden } = orderAndSplitColumns(cols, new Set(['c', 'a']))
    expect(visible.map((c) => c.key)).toEqual([NAME, 'b', 'd'])
    expect(hidden.map((c) => c.key)).toEqual(['a', 'c'])
  })

  it('does not mutate the input array', () => {
    const cols = [col(NAME, 'Name', true), col('b', 'Bravo'), col('a', 'Alpha')]
    const snapshot = cols.map((c) => c.key)
    orderAndSplitColumns(cols, new Set(['a']))
    expect(cols.map((c) => c.key)).toEqual(snapshot)
  })
})

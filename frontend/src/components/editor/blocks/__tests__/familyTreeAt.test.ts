import { describe, it, expect } from 'vitest'
import { latest, segmentAt, toMinute, treeAt, visibleAt } from '../familyTreeAt'
import { node, sampleTree } from './familyTreeFixtures'

describe('toMinute', () => {
  it('turns dates and timestamps into minutes', () => {
    expect(toMinute('2000-07-28')).toBe('2000-07-28T00:00')
    expect(toMinute('2000-07-28T08:05:59')).toBe('2000-07-28T08:05')
    expect(toMinute('')).toBeNull()
    expect(toMinute('not a date')).toBeNull()
  })
})

describe('segments', () => {
  const segments = [
    { start: null, end: '2010-12-31T23:59' },
    { start: '2011-01-01T00:00', end: null },
  ]

  it('picks the segment that holds, or none', () => {
    expect(segmentAt(segments, '2000-07-28T00:00')).toBe(segments[0])
    expect(segmentAt(segments, '2045-07-17T00:00')).toBe(segments[1])
    expect(segmentAt([{ start: '2011-01-01T00:00', end: '2011-01-31T23:59' }], '2045-07-17T00:00')).toBeNull()
  })

  it('takes the latest segment in the overall view', () => {
    expect(latest(segments)).toBe(segments[1])
    expect(segmentAt(segments, null)).toBe(segments[1])
  })
})

describe('treeAt', () => {
  it('shows everybody in the overall view, with the latest nuances and slots', () => {
    const state = treeAt(sampleTree(), null)
    expect(state.nodes.map((n) => n.node.id)).toEqual(['Person A', 'Person B', 'Person C', 'Person D'])
    expect(state.nodes.some((n) => n.deceased || n.dateUnknown)).toBe(false)
    expect(state.parents.find((p) => p.parent === 'Person B')?.base).toBe('kin.parent.adoptive')
    expect(state.nodes[2].properties).toEqual([{ name: 'Property A', tokens: [{ text: 'Value B' }] }])
    // Overlays carry no labels in the overall view.
    expect(state.overlays).toEqual([{ overlay: 0, source: 'Person C', target: 'Person D', label: null }])
  })

  it('hides people born later and links that do not hold yet', () => {
    const state = treeAt(sampleTree(), '2018-01-01T00:00')
    expect(state.nodes.map((n) => n.node.id)).toEqual(['Person A', 'Person B'])
    expect(state.parents).toEqual([])
    expect(state.partners).toHaveLength(1)
    expect(state.nodes[1].dateUnknown).toBe(true)
  })

  it('greys out the dead, switches nuances and labels overlays at an instant', () => {
    const early = treeAt(sampleTree(), '2022-01-01T00:00')
    expect(early.parents.find((p) => p.parent === 'Person B')?.label).toBe('foster')
    expect(early.nodes.find((n) => n.node.id === 'Person C')?.properties[0].tokens).toEqual([{ text: 'Value A' }])
    // The outsider shows only while its overlay link holds.
    expect(early.nodes.some((n) => n.node.id === 'Person D')).toBe(false)

    const late = treeAt(sampleTree(), '2031-01-01T00:00')
    expect(late.nodes.find((n) => n.node.id === 'Person A')?.deceased).toBe(true)
    expect(late.partners).toEqual([])
    expect(late.overlays[0].label).toBe('Label A')
    expect(late.nodes.some((n) => n.node.id === 'Person D')).toBe(true)
  })

  it('leaves invalid overlays out', () => {
    expect(treeAt(sampleTree(), null).overlays.every((o) => o.overlay === 0)).toBe(true)
  })

  it('always shows virtual parents', () => {
    expect(visibleAt(node('virtual:x', -1, { virtual: true }), '1900-01-01T00:00')).toBe(true)
  })
})

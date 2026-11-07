import { describe, it, expect } from 'vitest'
import {
  AVATAR_SIZE,
  NODE_WIDTH,
  OUTSIDER_GAP,
  directPath,
  layoutTree,
  longestPropertyText,
  nodeHeight,
  orderRows,
  overlayColor,
  parentPath,
  parentStyle,
  partnerStyle,
  pathMiddle,
  wrapText,
} from '../familyTreeLayout'
import { node, sampleTree } from './familyTreeFixtures'
import type { FamilyTree } from '@/composables/useFamilyTree'

function tree(partial: Partial<FamilyTree>): FamilyTree {
  return {
    root: null, root_valid: true, scope: 'lineage', nodes: [], parent_edges: [],
    partner_edges: [], virtual_edges: [], overlays: [], ...partial,
  }
}

describe('orderRows', () => {
  it('keeps partners together and children under their parents', () => {
    const t = tree({
      nodes: [
        node('Person A', 0, { birth: '1990-01-01T00:00' }),
        node('Person X', 0, { birth: '1991-01-01T00:00' }),
        node('Person B', 0, { birth: '1992-01-01T00:00' }),
        node('Person C', 1),
        node('Person D', 1),
      ],
      partner_edges: [{ a: 'Person A', b: 'Person B', segments: [] }],
      parent_edges: [
        { parent: 'Person X', child: 'Person C', anchor: true, broken: false, segments: [] },
        { parent: 'Person A', child: 'Person D', anchor: true, broken: false, segments: [] },
      ],
    })
    const rows = orderRows(t)
    const top = rows.get(0) as string[]
    expect(Math.abs(top.indexOf('Person A') - top.indexOf('Person B'))).toBe(1)
    expect(rows.get(1)).toEqual(['Person D', 'Person C'])
  })
})

describe('layoutTree', () => {
  it('places rows top to bottom and outsiders in a column on the right', () => {
    const layout = layoutTree(sampleTree())
    const a = layout.boxes.get('Person A')!
    const c = layout.boxes.get('Person C')!
    const d = layout.boxes.get('Person D')!
    expect(c.y).toBeGreaterThan(a.y)
    expect(d.x).toBeGreaterThanOrEqual(Math.max(a.x, c.x) + NODE_WIDTH + OUTSIDER_GAP)
    expect(layout.width).toBe(d.x + NODE_WIDTH)
  })

  it('puts a child under the middle of its parents', () => {
    const layout = layoutTree(sampleTree())
    const a = layout.boxes.get('Person A')!
    const b = layout.boxes.get('Person B')!
    const c = layout.boxes.get('Person C')!
    expect(c.x + c.width / 2).toBeCloseTo((a.x + b.x + NODE_WIDTH) / 2)
  })

  it('sizes nodes by their longest property value in any period', () => {
    const tall = node('Person A', 0, {
      properties: [{ schema_id: 's', name: 'P', slots: [
        { start: null, end: null, tokens: [{ text: 'x'.repeat(80) }] },
        { start: null, end: null, tokens: [{ text: 'y' }] },
      ] }],
    })
    expect(longestPropertyText(tall)).toBe(82)
    expect(nodeHeight(tall)).toBeGreaterThan(nodeHeight(node('Person B', 0)))
    expect(nodeHeight(node('virtual:x', 0, { virtual: true }))).toBeLessThan(AVATAR_SIZE)
  })
})

describe('link styles', () => {
  it('draws each nuance in its own way and unknown ones as unspecified', () => {
    expect(parentStyle('kin.parent.biological').dash).toBeNull()
    expect(parentStyle('kin.parent.putative').marked).toBe(true)
    expect(parentStyle('kin.parent.step').dash).toBe('1 4')
    expect(parentStyle(null).muted).toBe(true)
    expect(partnerStyle('kin.partner.concubinage')).toMatchObject({ dash: '1 4', marked: true })
    expect(partnerStyle('unknown').muted).toBe(true)
    expect(overlayColor('green')).toBe('#32b450')
    expect(overlayColor(null)).toBe(overlayColor('default'))
  })

  it('routes a parent link across above the child and finds its middle', () => {
    const parent = { id: 'p', x: 0, y: 0, width: 100, height: 120, generation: 0 }
    const child = { id: 'c', x: 200, y: 200, width: 100, height: 120, generation: 1 }
    const path = parentPath(parent, child)
    expect(path.startsWith('M 50 120')).toBe(true)
    expect(path.endsWith('L 250 200')).toBe(true)
    expect(pathMiddle(path)).toEqual({ x: 150, y: 168 })
  })

  it('runs a direct link from the edge of one picture to the edge of the other', () => {
    const a = { id: 'a', x: 0, y: 0, width: 100, height: 120, generation: 0 }
    const b = { id: 'b', x: 300, y: 0, width: 100, height: 120, generation: 0 }
    const half = AVATAR_SIZE / 2
    expect(directPath(a, b)).toBe(`M ${50 + half + 2} ${half} L ${350 - half - 2} ${half}`)
  })
})

describe('wrapText', () => {
  it('breaks at spaces and cuts words longer than a line', () => {
    expect(wrapText('Value A, Value B', 9)).toEqual(['Value A,', 'Value B'])
    expect(wrapText('abcdefghij', 4)).toEqual(['abcd', 'efgh', 'ij'])
    expect(wrapText('')).toEqual([])
  })
})

import { describe, it, expect } from 'vitest'
import {
  BOW_DROP,
  PICTURE_HEIGHT,
  PICTURE_WIDTH,
  bowMiddle,
  bowPath,
  bowY,
  groupLinks,
  stemPath,
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
    expect(nodeHeight(node('virtual:x', 0, { virtual: true }))).toBeLessThan(PICTURE_HEIGHT)
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
    const y = PICTURE_HEIGHT / 2
    expect(directPath(a, b)).toBe(`M ${50 + PICTURE_WIDTH / 2 + 2} ${y} L ${350 - PICTURE_WIDTH / 2 - 2} ${y}`)
    const below = { id: 'c', x: 0, y: 300, width: 100, height: 120, generation: 1 }
    // Straight down: the line leaves through the bottom edge of the picture.
    expect(directPath(a, below)).toBe(`M 50 ${y + PICTURE_HEIGHT / 2 + 2} L 50 ${300 + y - PICTURE_HEIGHT / 2 - 2}`)
  })
})

describe('wrapText', () => {
  it('breaks at spaces and cuts words longer than a line', () => {
    expect(wrapText('Value A, Value B', 9)).toEqual(['Value A,', 'Value B'])
    expect(wrapText('abcdefghij', 4)).toEqual(['abcd', 'efgh', 'ij'])
    expect(wrapText('')).toEqual([])
  })
})

describe('couples', () => {
  const link = (parent: string, child: string, base: string | null) => ({
    parent, child, anchor: false, broken: false, base, label: null,
    segment: { start: null, end: null, base },
  })
  const partner = (a: string, b: string) => ({
    a, b, base: 'kin.partner.marriage', label: null, segment: { start: null, end: null },
  })
  const rows: Record<string, number> = { 'Person A': 0, 'Person B': 0, 'Person X': 1, 'Person C': 1, 'Person D': 1 }

  it('joins partners with shared children of one nuance and leaves the rest alone', () => {
    const groups = groupLinks({
      nodes: [], virtual: [], overlays: [],
      partners: [partner('Person A', 'Person B'), partner('Person A', 'Person X')],
      parents: [
        link('Person A', 'Person C', 'kin.parent.biological'),
        link('Person B', 'Person C', 'kin.parent.biological'),
        link('Person A', 'Person D', 'kin.parent.biological'),
        link('Person B', 'Person D', 'kin.parent.step'),
      ],
    }, (id) => rows[id])
    expect(groups.couples).toHaveLength(1)
    expect(groups.couples[0].children.map((c) => c.child)).toEqual(['Person C'])
    // Different nuances to the two parents: two lines of their own.
    expect(groups.parents.map((l) => `${l.parent}>${l.child}`)).toEqual(['Person A>Person D', 'Person B>Person D'])
    // Different rows: no couple, the straight line stays.
    expect(groups.partners.map((p) => p.b)).toEqual(['Person X'])
  })

  it('draws the bow under both and the line to the child from its middle', () => {
    const a = { id: 'a', x: 0, y: 0, width: 100, height: 120, generation: 0 }
    const b = { id: 'b', x: 200, y: 0, width: 100, height: 100, generation: 0 }
    const child = { id: 'c', x: 100, y: 200, width: 100, height: 120, generation: 1 }
    const y = 120 + BOW_DROP
    expect(bowPath(b, a, y)).toBe(`M 50 120 L 50 ${y} L 250 ${y} L 250 100`)
    const middle = bowMiddle(a, b, y)
    expect(middle).toEqual({ x: 150, y })
    expect(stemPath(middle, child)).toBe(`M 150 ${y} L 150 168 L 150 168 L 150 200`)
  })

  it('lowers further bows of a row a little, staying above the children\'s lines', () => {
    expect(bowY(100, 0)).toBe(100 + BOW_DROP)
    expect(bowY(100, 1)).toBeGreaterThan(bowY(100, 0))
    for (let i = 0; i < 10; i++) expect(bowY(100, i)).toBeLessThan(100 + 64 / 2)
  })

  it('reports the bottom of every row', () => {
    const layout = layoutTree(sampleTree())
    const a = layout.boxes.get('Person A')!
    expect(layout.rowBottoms.get(-1)).toBeGreaterThanOrEqual(a.y + a.height)
  })
})

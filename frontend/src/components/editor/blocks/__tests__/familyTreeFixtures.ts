/**
 * A small family tree as the server sends it, for the family tree tests.
 * Person A and Person B are the parents of Person C; Person D is an
 * outsider linked to Person C through an overlay.
 */
import type { FamilyTree, TreeNode } from '@/composables/useFamilyTree'

export function node(id: string, generation: number | null, extra: Partial<TreeNode> = {}): TreeNode {
  return {
    id,
    title: id,
    icon: null,
    avatar: null,
    generation,
    outsider: false,
    virtual: false,
    birth: null,
    death: null,
    birth_value: null,
    death_value: null,
    sex: null,
    warnings: [],
    properties: [],
    ...extra,
  }
}

export function sampleTree(): FamilyTree {
  return {
    root: 'Person C',
    root_valid: true,
    scope: 'lineage',
    nodes: [
      node('Person A', -1, { birth: '1990-01-01T00:00', death: '2030-05-01T00:00' }),
      node('Person B', -1),
      node('Person C', 0, {
        birth: '2020-06-01T00:00',
        properties: [{
          schema_id: 's-1',
          name: 'Property A',
          slots: [
            { start: null, end: '2025-12-31T23:59', tokens: [{ text: 'Value A' }] },
            { start: '2026-01-01T00:00', end: null, tokens: [{ text: 'Value B' }] },
          ],
        }],
      }),
      node('Person D', null, { outsider: true }),
    ],
    parent_edges: [
      {
        parent: 'Person A', child: 'Person C', anchor: true, broken: false,
        segments: [{ start: '2020-06-01T00:00', end: null, base: 'kin.parent.biological', label: 'biological' }],
      },
      {
        parent: 'Person B', child: 'Person C', anchor: false, broken: false,
        segments: [
          { start: null, end: '2024-12-31T23:59', base: 'kin.parent.foster', label: 'foster' },
          { start: '2025-01-01T00:00', end: null, base: 'kin.parent.adoptive', label: 'adoptive' },
        ],
      },
    ],
    partner_edges: [
      { a: 'Person A', b: 'Person B', segments: [{ start: '2015-01-01T00:00', end: '2030-05-01T00:00', base: 'kin.partner.marriage', label: 'marriage' }] },
    ],
    virtual_edges: [],
    overlays: [
      {
        schema_id: 's-2', name: 'Relation A', valid: true, direction: 'both', color: 'green',
        show_outsiders: true, show_nuances: true,
        edges: [{ source: 'Person C', target: 'Person D', segments: [{ start: '2028-01-01T00:00', end: null, label: 'Label A' }] }],
      },
      {
        schema_id: 's-3', name: null, valid: false, direction: 'forward', color: null,
        show_outsiders: false, show_nuances: false, edges: [],
      },
    ],
  }
}

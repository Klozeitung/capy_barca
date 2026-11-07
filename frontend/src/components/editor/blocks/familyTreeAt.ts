/**
 * familyTreeAt
 *
 * What a family tree shows at one instant. The server sends the tree for
 * every point in time (``useFamilyTree``); this module picks what holds at
 * *at*, a wall-clock minute, or gives the overall view when *at* is null.
 * Today the instant is set in the view settings (``familyTreeAt``); a slider
 * can pass its own instant to the same function without a new request.
 *
 * At an instant
 * -------------
 * - A person born after *at* is hidden; a person without a date of birth is
 *   shown with the hint "date unknown". A person who died at or before *at*
 *   is shown greyed out.
 * - A link is shown while one of its segments holds; it is drawn in the
 *   style of that segment's nuance, with its label where asked for.
 * - A property with a timeline shows the slot that holds.
 *
 * Overall view
 * ------------
 * Everybody is shown and nobody greyed out. Every link is shown in the style
 * of its latest segment; overlays carry no labels. Properties with a
 * timeline show their latest slot, like rollups and formulas read them.
 */
import type {
  FamilyTree,
  TreeNode,
  TreeProperty,
  TreeSegment,
  TreeToken,
} from '@/composables/useFamilyTree'

export interface NodeAt {
  node: TreeNode
  deceased: boolean
  dateUnknown: boolean
  /** One entry per property under the life span; empty ones are left out. */
  properties: { name: string; tokens: TreeToken[] }[]
}

export interface LinkAt {
  /** The segment that holds, or the latest one in the overall view. */
  segment: TreeSegment
  /** Base nuance id of that segment, null for unspecified. */
  base: string | null
  label: string | null
}

export interface ParentLinkAt extends LinkAt {
  parent: string
  child: string
  anchor: boolean
  broken: boolean
}

export interface PartnerLinkAt extends LinkAt {
  a: string
  b: string
}

export interface OverlayLinkAt {
  overlay: number
  source: string
  target: string
  label: string | null
}

export interface TreeAt {
  nodes: NodeAt[]
  parents: ParentLinkAt[]
  partners: PartnerLinkAt[]
  virtual: { parent: string; child: string }[]
  overlays: OverlayLinkAt[]
}

/**
 * *raw* (a date, or a timestamp with or without seconds) as a minute,
 * or null when it is empty or malformed.
 */
export function toMinute(raw: string | null | undefined): string | null {
  if (!raw) return null
  const match = /^(\d{4}-\d{2}-\d{2})(?:T(\d{2}:\d{2}))?/.exec(raw.trim())
  if (!match) return null
  return `${match[1]}T${match[2] ?? '00:00'}`
}

function covers(segment: { start: string | null; end: string | null }, at: string): boolean {
  return (segment.start === null || segment.start <= at) && (segment.end === null || at <= segment.end)
}

/** The latest of *segments* by start; open starts count as the earliest. */
export function latest<T extends { start: string | null }>(segments: T[]): T | null {
  let best: T | null = null
  for (const segment of segments) {
    if (best === null || (segment.start ?? '') >= (best.start ?? '')) best = segment
  }
  return best
}

/** The segment of *segments* that holds at *at*, or the latest when *at* is null. */
export function segmentAt<T extends { start: string | null; end: string | null }>(
  segments: T[],
  at: string | null,
): T | null {
  if (at === null) return latest(segments)
  return segments.find((s) => covers(s, at)) ?? null
}

function propertyAt(property: TreeProperty, at: string | null): TreeToken[] {
  if (property.slots) return segmentAt(property.slots, at)?.tokens ?? []
  return property.tokens ?? []
}

/** Whether *node* is shown at *at*: born by then, or birth unknown. */
export function visibleAt(node: TreeNode, at: string | null): boolean {
  if (at === null || node.virtual) return true
  return node.birth === null || node.birth <= at
}

export function treeAt(tree: FamilyTree, at: string | null): TreeAt {
  const nodes: NodeAt[] = tree.nodes
    .filter((node) => visibleAt(node, at))
    .map((node) => ({
      node,
      deceased: at !== null && node.death !== null && node.death <= at,
      dateUnknown: at !== null && !node.virtual && node.birth === null,
      properties: node.properties
        .map((p) => ({ name: p.name, tokens: propertyAt(p, at) }))
        .filter((p) => p.tokens.length > 0),
    }))
  const shown = new Set(nodes.map((n) => n.node.id))

  const parents: ParentLinkAt[] = []
  for (const edge of tree.parent_edges) {
    if (!shown.has(edge.parent) || !shown.has(edge.child)) continue
    const segment = segmentAt(edge.segments, at)
    if (!segment) continue
    parents.push({
      parent: edge.parent,
      child: edge.child,
      anchor: edge.anchor,
      broken: edge.broken,
      segment,
      base: segment.base ?? null,
      label: segment.label ?? null,
    })
  }

  const partners: PartnerLinkAt[] = []
  for (const edge of tree.partner_edges) {
    if (!shown.has(edge.a) || !shown.has(edge.b)) continue
    const segment = segmentAt(edge.segments, at)
    if (!segment) continue
    partners.push({ a: edge.a, b: edge.b, segment, base: segment.base ?? null, label: segment.label ?? null })
  }

  const overlays: OverlayLinkAt[] = []
  tree.overlays.forEach((overlay, index) => {
    if (!overlay.valid) return
    for (const edge of overlay.edges) {
      if (!shown.has(edge.source) || !shown.has(edge.target)) continue
      const segment = segmentAt(edge.segments, at)
      if (!segment) continue
      overlays.push({
        overlay: index,
        source: edge.source,
        target: edge.target,
        label: at !== null && overlay.show_nuances ? segment.label ?? null : null,
      })
    }
  })

  const virtual = tree.virtual_edges.filter((e) => shown.has(e.parent) && shown.has(e.child))
  // Outsiders show only while an overlay link of theirs holds.
  const linked = new Set(overlays.flatMap((o) => [o.source, o.target]))
  const visibleNodes = nodes.filter((n) => !n.node.outsider || linked.has(n.node.id))
  return { nodes: visibleNodes, parents, partners, virtual, overlays }
}

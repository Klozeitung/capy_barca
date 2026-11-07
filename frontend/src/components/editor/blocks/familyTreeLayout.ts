/**
 * familyTreeLayout
 *
 * Where the family tree view draws each person, and how it draws each link.
 * The server decides the row (generation) of every person; this module
 * decides the order within a row and the coordinates.
 *
 * Stable positions
 * ----------------
 * The layout is computed once over everybody in the tree's scope, not over
 * the people shown at an instant. A person born after the instant leaves a
 * gap, and everybody else keeps their place, so moving through time (a
 * slider later) does not make the drawing jump. Node heights are taken from
 * the longest value a property has in any period for the same reason.
 *
 * Order within a row
 * ------------------
 * Partners in the same row stay together as one unit. Units start in the
 * order of birth, then title, and are re-sorted a few times by the mean
 * position of their parents (sweeping down) and of their children (sweeping
 * up): a barycenter heuristic, which keeps lines short and crossings few
 * without any dependency.
 *
 * Coordinates
 * -----------
 * Rows are placed top to bottom, each as high as its highest node, so the
 * profile pictures of a generation share one height. Pictures are 3:4
 * portrait rectangles. Within a row a node moves under the middle of its
 * parents where there is room. People outside the family who come in
 * through an overlay stand in a column on the right.
 *
 * Couples
 * -------
 * Partners in one row who have children shown together are joined by a bow
 * under both of them, in the style of their partnership, and the line to
 * those children leaves from the middle of the bow (``groupLinks``). A child
 * whose links to the two differ in nuance keeps two separate lines, so no
 * nuance is lost; partners without shared children keep the straight line
 * between their pictures.
 *
 * Channels
 * --------
 * Lines to a row run across in the gap above it, its channel. All lines that
 * leave one point (a person, the middle of a bow, a virtual parent) form a
 * net and share one track. The tracks are assigned with the left-edge
 * algorithm of channel routing with vertical constraints (``assignTracks``):
 * nets whose horizontal runs overlap or touch take different tracks, and a
 * net that leaves right above the end of another net runs higher, so the two
 * vertical lines never meet. The gap grows by ``TRACK_STEP`` for every track
 * beyond the first; it is reserved from the overall view so positions stay
 * stable, and an instant that needs more tracks than reserved packs them
 * closer (``routeAcross``).
 */
import type { FamilyTree, TreeNode, TreeToken } from '@/composables/useFamilyTree'
import { treeAt, type ParentLinkAt, type PartnerLinkAt, type TreeAt } from './familyTreeAt'

export const NODE_WIDTH = 168
export const PICTURE_WIDTH = 48
export const PICTURE_HEIGHT = 64
export const VIRTUAL_WIDTH = 21
export const VIRTUAL_HEIGHT = 28
/** Corner radius of pictures and placeholders. */
export const PICTURE_RADIUS = 4
/** How far a couple's bow runs below the lowest node of its row. */
export const BOW_DROP = 14
/**
 * Further couples of a row run a little lower each, so two bows that share
 * a person (someone with children by two partners) stay apart; at most
 * ``BOW_LEVELS`` levels, which keeps every bow above the children's lines.
 */
export const BOW_STEP = 5
export const BOW_LEVELS = 4

/** The height of the bow of the *index*-th couple of a row whose nodes end at *bottom*. */
export function bowY(bottom: number, index: number): number {
  return bottom + BOW_DROP + BOW_STEP * (index % BOW_LEVELS)
}
export const H_GAP = 28
export const V_GAP = 64
export const LINE_HEIGHT = 15
export const OUTSIDER_GAP = 96
/** Vertical distance between two tracks of a channel. */
export const TRACK_STEP = 10
/** Horizontal distance under which two lines count as touching. */
export const TRACK_TOUCH = 4
/** Shortest vertical line from a track down to a child when tracks are packed. */
export const MIN_DROP = 12
const CHARS_PER_LINE = 26
const SWEEPS = 4

export interface Box {
  id: string
  x: number
  y: number
  width: number
  height: number
  generation: number | null
}

/** The gap above a row where lines to it run across. */
export interface Channel {
  /** Height of the first track. */
  top: number
  /** Top of the row below the channel. */
  bottom: number
  /** Tracks reserved for the channel, at least one. */
  tracks: number
}

export interface TreeLayout {
  boxes: Map<string, Box>
  /** Lowest point of each row's nodes, by generation. */
  rowBottoms: Map<number, number>
  /** The channel above each row but the first, by generation. */
  channels: Map<number, Channel>
  width: number
  height: number
}

/** Lines a text of *length* characters takes in a node. */
function lines(length: number): number {
  return Math.max(1, Math.ceil(length / CHARS_PER_LINE))
}

function tokenLength(token: TreeToken): number {
  if ('text' in token) return token.text.length
  if ('date' in token) return token.end ? 23 : 10
  return 1
}

/** The longest property line *node* has in any period, in characters. */
export function longestPropertyText(node: TreeNode): number {
  let total = 0
  for (const property of node.properties) {
    const variants = property.slots ? property.slots.map((s) => s.tokens) : [property.tokens ?? []]
    const longest = Math.max(0, ...variants.map((tokens) => tokens.reduce((n, t) => n + tokenLength(t) + 2, 0)))
    total += longest
  }
  return total
}

/** Height of a node: picture, title, life span and the property line. */
export function nodeHeight(node: TreeNode): number {
  if (node.virtual) return VIRTUAL_HEIGHT
  const title = lines((node.title ?? '').length)
  const properties = longestPropertyText(node)
  return PICTURE_HEIGHT + 6 + title * LINE_HEIGHT + LINE_HEIGHT + (properties ? lines(properties) * LINE_HEIGHT : 0) + 4
}

function nodeWidth(node: TreeNode): number {
  return node.virtual ? VIRTUAL_WIDTH : NODE_WIDTH
}

function sortKey(node: TreeNode): string {
  return `${node.birth ?? '9999'}|${(node.title ?? '').toLocaleLowerCase()}|${node.id}`
}

function mean(values: number[]): number | null {
  return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null
}

/**
 * The order of every row: generation to node ids, left to right. Partners
 * in a row are kept next to each other.
 */
export function orderRows(tree: FamilyTree): Map<number, string[]> {
  const nodes = tree.nodes.filter((n) => !n.outsider && n.generation !== null)
  const byId = new Map(nodes.map((n) => [n.id, n]))
  const rows = new Map<number, TreeNode[]>()
  for (const node of nodes) {
    const list = rows.get(node.generation as number) ?? []
    list.push(node)
    rows.set(node.generation as number, list)
  }

  const parentsOf = new Map<string, string[]>()
  const childrenOf = new Map<string, string[]>()
  const link = (parent: string, child: string) => {
    if (!byId.has(parent) || !byId.has(child)) return
    parentsOf.set(child, [...(parentsOf.get(child) ?? []), parent])
    childrenOf.set(parent, [...(childrenOf.get(parent) ?? []), child])
  }
  for (const edge of tree.parent_edges) link(edge.parent, edge.child)
  for (const edge of tree.virtual_edges) link(edge.parent, edge.child)

  // Units: partners within one row, joined.
  const unitOf = new Map<string, string>()
  const find = (id: string): string => {
    let current = id
    while (unitOf.get(current) !== current) current = unitOf.get(current) as string
    return current
  }
  for (const node of nodes) unitOf.set(node.id, node.id)
  for (const edge of tree.partner_edges) {
    const a = byId.get(edge.a)
    const b = byId.get(edge.b)
    if (!a || !b || a.generation !== b.generation) continue
    const ra = find(edge.a)
    const rb = find(edge.b)
    if (ra !== rb) unitOf.set(rb, ra)
  }

  const generations = [...rows.keys()].sort((a, b) => a - b)
  const order = new Map<number, string[][]>()
  for (const generation of generations) {
    const members = (rows.get(generation) as TreeNode[]).slice().sort((a, b) => sortKey(a).localeCompare(sortKey(b)))
    const units = new Map<string, string[]>()
    for (const node of members) {
      const root = find(node.id)
      units.set(root, [...(units.get(root) ?? []), node.id])
    }
    order.set(generation, [...units.values()])
  }

  const index = new Map<string, number>()
  const reindex = (generation: number) => {
    let i = 0
    for (const unit of order.get(generation) ?? []) for (const id of unit) index.set(id, i++)
  }
  generations.forEach(reindex)

  const sweep = (generation: number, neighbours: Map<string, string[]>) => {
    const units = order.get(generation) ?? []
    const keyed = units.map((unit, position) => {
      const positions = unit.flatMap((id) => (neighbours.get(id) ?? []).map((n) => index.get(n) ?? 0))
      return { unit, key: mean(positions) ?? position, position }
    })
    keyed.sort((a, b) => a.key - b.key || a.position - b.position)
    order.set(generation, keyed.map((k) => k.unit))
    reindex(generation)
  }
  for (let i = 0; i < SWEEPS; i++) {
    for (const generation of generations.slice(1)) sweep(generation, parentsOf)
    for (const generation of generations.slice(0, -1).reverse()) sweep(generation, childrenOf)
  }

  return new Map(generations.map((g) => [g, (order.get(g) ?? []).flat()]))
}

/** Coordinates of every node of *tree* (see the module). */
export function layoutTree(tree: FamilyTree): TreeLayout {
  const byId = new Map(tree.nodes.map((n) => [n.id, n]))
  const rows = orderRows(tree)
  const boxes = new Map<string, Box>()
  const rowBottoms = new Map<number, number>()
  const parentsOf = new Map<string, string[]>()
  for (const edge of [...tree.parent_edges, ...tree.virtual_edges]) {
    parentsOf.set(edge.child, [...(parentsOf.get(edge.child) ?? []), edge.parent])
  }

  const channels = new Map<number, Channel>()
  // The lines of the overall view decide how many tracks each channel reserves.
  const generationOf = (id: string) => {
    const node = byId.get(id)
    return node && !node.outsider ? node.generation : null
  }
  const overall = groupLinks(treeAt(tree, null), generationOf)

  let y = 0
  let previousBottom: number | null = null
  for (const [generation, ids] of rows) {
    const height = Math.max(...ids.map((id) => nodeHeight(byId.get(id) as TreeNode)))
    let right = -H_GAP
    const placed: { id: string; node: TreeNode; x: number; width: number }[] = []
    for (const id of ids) {
      const node = byId.get(id) as TreeNode
      const width = nodeWidth(node)
      const parentCenters = (parentsOf.get(id) ?? [])
        .map((p) => boxes.get(p))
        .filter((b): b is Box => !!b)
        .map((b) => b.x + b.width / 2)
      const wanted = mean(parentCenters)
      const x = Math.max(right + H_GAP, wanted === null ? right + H_GAP : wanted - width / 2)
      placed.push({ id, node, x, width })
      right = x + width
    }

    if (previousBottom !== null) {
      const centers = new Map(placed.map((p) => [p.id, p.x + p.width / 2]))
      const items = overallRoutes(overall, tree, boxes, centers, generationOf, generation)
      const tracks = Math.max(1, assignTracks(items).count)
      y = previousBottom + V_GAP + (tracks - 1) * TRACK_STEP
      channels.set(generation, { top: previousBottom + V_GAP / 2, bottom: y, tracks })
    }

    for (const { id, node, x, width } of placed) {
      // A virtual parent sits level with the pictures of its row.
      const top = node.virtual ? y + (PICTURE_HEIGHT - VIRTUAL_HEIGHT) / 2 : y
      boxes.set(id, { id, x, y: top, width, height: nodeHeight(node), generation })
    }
    rowBottoms.set(generation, y + height)
    previousBottom = y + height
  }

  const minX = Math.min(0, ...[...boxes.values()].map((b) => b.x))
  for (const box of boxes.values()) box.x -= minX
  let width = Math.max(0, ...[...boxes.values()].map((b) => b.x + b.width))

  let height = previousBottom ?? 0
  const outsiders = tree.nodes.filter((n) => n.outsider).sort((a, b) => sortKey(a).localeCompare(sortKey(b)))
  if (outsiders.length) {
    const x = width + OUTSIDER_GAP
    let top = 0
    for (const node of outsiders) {
      const nodeH = nodeHeight(node)
      boxes.set(node.id, { id: node.id, x, y: top, width: NODE_WIDTH, height: nodeH, generation: null })
      top += nodeH + H_GAP
    }
    width = x + NODE_WIDTH
    height = Math.max(height, top)
  }
  return { boxes, rowBottoms, channels, width, height }
}

/**
 * The lines of the overall view *groups* that end in row *generation*, for
 * the reservation of its channel. Sources are placed already; the row's own
 * nodes are not yet, their centres come in *centers*.
 */
function overallRoutes(
  groups: LinkGroups,
  tree: FamilyTree,
  boxes: Map<string, Box>,
  centers: Map<string, number>,
  generationOf: (id: string) => number | null,
  generation: number,
): RouteItem[] {
  const items: RouteItem[] = []
  const add = (key: string, net: string, source: Box | undefined, sourceX: number | null, child: string) => {
    const dropX = centers.get(child)
    if (!source || sourceX === null || dropX === undefined) return
    if (source.generation === null || source.generation >= generation) return
    items.push({ key, net, row: generation, sourceX, dropX })
  }
  for (const link of groups.parents) {
    const source = boxes.get(link.parent)
    add(`p:${link.parent}:${link.child}`, `p:${link.parent}`, source, source ? nodeBottom(source).x : null, link.child)
  }
  for (const couple of groups.couples) {
    const a = boxes.get(couple.partner.a)
    const b = boxes.get(couple.partner.b)
    const middle = a && b ? bowMiddle(a, b, 0).x : null
    for (const { child } of couple.children) {
      add(`s:${couple.partner.a}:${couple.partner.b}:${child}`, `c:${couple.partner.a}:${couple.partner.b}`, a, middle, child)
    }
  }
  for (const edge of tree.virtual_edges) {
    if (generationOf(edge.child) !== generation) continue
    const source = boxes.get(edge.parent)
    add(`v:${edge.parent}:${edge.child}`, `v:${edge.parent}`, source, source ? nodeBottom(source).x : null, edge.child)
  }
  return items
}

// ─── Channel routing ─────────────────────────────────────────────────────────

/** One line that runs across a channel: from its net's source down to a child. */
export interface RouteItem {
  /** Unique key of the line. */
  key: string
  /** The point the line leaves from; lines of one net share a track. */
  net: string
  /** Generation of the child's row, the channel the line runs in. */
  row: number
  sourceX: number
  dropX: number
}

interface Net {
  id: string
  row: number
  sourceX: number
  drops: number[]
  left: number
  right: number
}

/**
 * Tracks for the nets of *items* (see the module): the track of every net,
 * keyed by row and net, and the largest number of tracks of any row.
 */
export function assignTracks(items: RouteItem[]): { tracks: Map<string, number>; count: number } {
  const byRow = new Map<number, Map<string, Net>>()
  for (const item of items) {
    const nets = byRow.get(item.row) ?? new Map<string, Net>()
    byRow.set(item.row, nets)
    const id = trackKey(item.row, item.net)
    const net = nets.get(id) ?? { id, row: item.row, sourceX: item.sourceX, drops: [], left: item.sourceX, right: item.sourceX }
    net.drops.push(item.dropX)
    net.left = Math.min(net.left, item.dropX)
    net.right = Math.max(net.right, item.dropX)
    nets.set(id, net)
  }

  const tracks = new Map<string, number>()
  let count = 0
  for (const nets of byRow.values()) {
    const list = [...nets.values()].sort((a, b) => a.left - b.left || a.right - b.right || a.id.localeCompare(b.id))
    // A net leaving right above the end of another one runs higher than it.
    const above = new Map<string, Set<string>>(list.map((n) => [n.id, new Set<string>()]))
    for (const upper of list) {
      for (const lower of list) {
        if (upper === lower) continue
        if (lower.drops.some((d) => Math.abs(d - upper.sourceX) < TRACK_TOUCH)) above.get(lower.id)?.add(upper.id)
      }
    }
    const occupied: Net[][] = []
    const done = new Set<string>()
    const place = (net: Net, constrained: boolean) => {
      let track = 0
      if (constrained) {
        for (const id of above.get(net.id) ?? []) track = Math.max(track, (tracks.get(id) ?? -1) + 1)
      }
      while ((occupied[track] ?? []).some((o) => o.left <= net.right + TRACK_TOUCH && net.left <= o.right + TRACK_TOUCH)) track++
      ;(occupied[track] ??= []).push(net)
      tracks.set(net.id, track)
      done.add(net.id)
    }
    // Left edge first, a net only once all nets above it have their tracks.
    let progress = true
    while (progress) {
      progress = false
      for (const net of list) {
        if (done.has(net.id)) continue
        if ([...(above.get(net.id) ?? [])].every((id) => done.has(id))) {
          place(net, true)
          progress = true
        }
      }
    }
    // Nets caught in a cycle of constraints: left edge only.
    for (const net of list) if (!done.has(net.id)) place(net, false)
    count = Math.max(count, occupied.length)
  }
  return { tracks, count }
}

function trackKey(row: number, net: string): string {
  return `${row}|${net}`
}

/**
 * The height at which every line of *items* runs across, by its key. Lines
 * outside any channel are left out and keep the default route.
 */
export function routeAcross(items: RouteItem[], channels: Map<number, Channel>): Map<string, number> {
  const inChannel = items.filter((item) => channels.has(item.row))
  const { tracks } = assignTracks(inChannel)
  const used = new Map<number, number>()
  for (const item of inChannel) {
    const track = tracks.get(trackKey(item.row, item.net)) ?? 0
    used.set(item.row, Math.max(used.get(item.row) ?? 0, track + 1))
  }
  const result = new Map<string, number>()
  for (const item of inChannel) {
    const channel = channels.get(item.row) as Channel
    const count = used.get(item.row) ?? 1
    const track = tracks.get(trackKey(item.row, item.net)) ?? 0
    const room = channel.bottom - MIN_DROP - channel.top
    const step = count > channel.tracks ? Math.min(TRACK_STEP, room / Math.max(1, count - 1)) : TRACK_STEP
    result.set(item.key, channel.top + track * step)
  }
  return result
}

// ─── Links ───────────────────────────────────────────────────────────────────

export interface LinkStyle {
  /** SVG stroke-dasharray, null for a solid line. */
  dash: string | null
  width: number
  muted: boolean
  /** Small mark in the middle of the line (putative parent, concubinage). */
  marked: boolean
}

const PARENT_STYLES: Record<string, LinkStyle> = {
  'kin.parent.biological': { dash: null, width: 2, muted: false, marked: false },
  'kin.parent.putative': { dash: null, width: 2, muted: false, marked: true },
  'kin.parent.legal': { dash: '8 4', width: 2, muted: false, marked: false },
  'kin.parent.adoptive': { dash: '5 4', width: 2, muted: false, marked: false },
  'kin.parent.foster': { dash: '3 3', width: 1.5, muted: false, marked: false },
  'kin.parent.step': { dash: '1 4', width: 2, muted: false, marked: false },
}

const PARTNER_STYLES: Record<string, LinkStyle> = {
  'kin.partner.marriage': { dash: null, width: 2, muted: false, marked: false },
  'kin.partner.engagement': { dash: '6 4', width: 2, muted: false, marked: false },
  'kin.partner.relationship': { dash: null, width: 1, muted: false, marked: false },
  'kin.partner.affair': { dash: '1 4', width: 2, muted: false, marked: false },
  'kin.partner.concubinage': { dash: '1 4', width: 2, muted: false, marked: true },
}

const UNSPECIFIED: LinkStyle = { dash: null, width: 1, muted: true, marked: false }

/** How a parent link of *base* nuance is drawn; no nuance is unspecified. */
export function parentStyle(base: string | null): LinkStyle {
  return (base && PARENT_STYLES[base]) || UNSPECIFIED
}

/** How a partnership of *base* nuance is drawn. */
export function partnerStyle(base: string | null): LinkStyle {
  return (base && PARTNER_STYLES[base]) || UNSPECIFIED
}

/** Line colours of the overlays, by the colour keys of select options. */
export const OVERLAY_COLORS: Record<string, string> = {
  default: '#2d82d2',
  gray: '#787878',
  red: '#dc4646',
  orange: '#dc8c28',
  yellow: '#c8b914',
  green: '#32b450',
  blue: '#2d82d2',
  purple: '#8c50c8',
  pink: '#dc50a0',
}

export function overlayColor(key: string | null | undefined): string {
  return OVERLAY_COLORS[key ?? 'default'] ?? OVERLAY_COLORS.default
}

/** Bottom centre of a node, below its text, where lines to its children start. */
export function nodeBottom(box: Box): { x: number; y: number } {
  return { x: box.x + box.width / 2, y: box.y + box.height }
}

/** Middle of a node's picture, where partner and overlay lines meet. */
export function pictureCenter(box: Box): { x: number; y: number } {
  return { x: box.x + box.width / 2, y: box.y + Math.min(PICTURE_HEIGHT, box.height) / 2 }
}

/** Distance from a picture's centre to its edge in the direction (*ux*, *uy*). */
function edgeDistance(box: Box, ux: number, uy: number): number {
  const halfWidth = Math.min(PICTURE_WIDTH, box.width) / 2
  const halfHeight = Math.min(PICTURE_HEIGHT, box.height) / 2
  const alongX = ux === 0 ? Infinity : halfWidth / Math.abs(ux)
  const alongY = uy === 0 ? Infinity : halfHeight / Math.abs(uy)
  return Math.min(alongX, alongY)
}

/**
 * The path of a parent link: down from below the parent's text, across
 * above the child's row at *across* (its track, see ``routeAcross``), down
 * to the child.
 */
export function parentPath(parent: Box, child: Box, across?: number): string {
  return stemPath(nodeBottom(parent), child, across)
}

/**
 * The path between the pictures of two people (partners, overlays), from
 * the edge of one picture to the edge of the other, so arrows stay visible.
 */
export function directPath(a: Box, b: Box): string {
  const from = pictureCenter(a)
  const to = pictureCenter(b)
  const dx = to.x - from.x
  const dy = to.y - from.y
  const length = Math.hypot(dx, dy)
  if (length === 0) return `M ${from.x} ${from.y} L ${to.x} ${to.y}`
  const ux = dx / length
  const uy = dy / length
  const start = edgeDistance(a, ux, uy) + 2
  const end = edgeDistance(b, ux, uy) + 2
  if (length <= start + end) return `M ${from.x} ${from.y} L ${to.x} ${to.y}`
  const round = (n: number) => Math.round(n * 10) / 10
  return `M ${round(from.x + ux * start)} ${round(from.y + uy * start)} L ${round(to.x - ux * end)} ${round(to.y - uy * end)}`
}

/**
 * The bow joining partners with shared children: down from below each of
 * them to *y*, under the row, and across.
 */
export function bowPath(a: Box, b: Box, y: number): string {
  const left = a.x <= b.x ? a : b
  const right = left === a ? b : a
  const from = nodeBottom(left)
  const to = nodeBottom(right)
  return `M ${from.x} ${from.y} L ${from.x} ${y} L ${to.x} ${y} L ${to.x} ${to.y}`
}

/** The middle of the bow between *a* and *b*, where the children's line starts. */
export function bowMiddle(a: Box, b: Box, y: number): { x: number; y: number } {
  return { x: (nodeBottom(a).x + nodeBottom(b).x) / 2, y }
}

/**
 * The line from a point (the middle of a bow) across above the child's row
 * at *across*, halfway up the gap without one, down to the child.
 */
export function stemPath(from: { x: number; y: number }, child: Box, across: number = child.y - V_GAP / 2): string {
  const to = { x: child.x + child.width / 2, y: child.y }
  if (across <= from.y || across >= to.y) return `M ${from.x} ${from.y} L ${to.x} ${to.y}`
  return `M ${from.x} ${from.y} L ${from.x} ${across} L ${to.x} ${across} L ${to.x} ${to.y}`
}

// ─── Grouping links ──────────────────────────────────────────────────────────

export interface CoupleGroup {
  partner: PartnerLinkAt
  /** The children the couple share, each with its two links of one nuance. */
  children: { child: string; links: [ParentLinkAt, ParentLinkAt] }[]
}

export interface LinkGroups {
  couples: CoupleGroup[]
  /** Partnerships without shared children shown: a straight line. */
  partners: PartnerLinkAt[]
  /** Parent links drawn on their own. */
  parents: ParentLinkAt[]
}

/**
 * The links of *state* sorted into couples with their shared children,
 * plain partnerships and single parent links (see the module). Partners
 * form a couple only within one row; *generation* tells the rows.
 */
export function groupLinks(state: TreeAt, generation: (id: string) => number | null | undefined): LinkGroups {
  const byPair = new Map<string, ParentLinkAt>()
  for (const link of state.parents) byPair.set(`${link.parent}|${link.child}`, link)
  const used = new Set<ParentLinkAt>()
  const couples: CoupleGroup[] = []
  const partners: PartnerLinkAt[] = []
  for (const partner of state.partners) {
    const row = generation(partner.a)
    const children: CoupleGroup['children'] = []
    if (row !== null && row !== undefined && row === generation(partner.b)) {
      for (const link of state.parents) {
        if (link.parent !== partner.a || used.has(link) || link.broken) continue
        const other = byPair.get(`${partner.b}|${link.child}`)
        if (!other || used.has(other) || other.broken || other.base !== link.base) continue
        children.push({ child: link.child, links: [link, other] })
        used.add(link)
        used.add(other)
      }
    }
    if (children.length) couples.push({ partner, children })
    else partners.push(partner)
  }
  return { couples, partners, parents: state.parents.filter((link) => !used.has(link)) }
}

/** The midpoint of a path built by the functions above, for marks and labels. */
export function pathMiddle(path: string): { x: number; y: number } {
  const numbers = path.match(/-?\d+(\.\d+)?/g)?.map(Number) ?? []
  const points: { x: number; y: number }[] = []
  for (let i = 0; i + 1 < numbers.length; i += 2) points.push({ x: numbers[i], y: numbers[i + 1] })
  if (points.length === 0) return { x: 0, y: 0 }
  if (points.length === 4) {
    return { x: (points[1].x + points[2].x) / 2, y: points[1].y }
  }
  const first = points[0]
  const last = points[points.length - 1]
  return { x: (first.x + last.x) / 2, y: (first.y + last.y) / 2 }
}

/**
 * *text* broken into lines of at most *width* characters at spaces, as a
 * node draws it; a word longer than a line is cut. Nothing is left out.
 */
export function wrapText(text: string, width: number = CHARS_PER_LINE): string[] {
  const result: string[] = []
  let line = ''
  for (const word of text.split(/\s+/).filter(Boolean)) {
    let rest = word
    while (rest.length > width) {
      if (line) {
        result.push(line)
        line = ''
      }
      result.push(rest.slice(0, width))
      rest = rest.slice(width)
    }
    if (!line) line = rest
    else if (line.length + 1 + rest.length <= width) line = `${line} ${rest}`
    else {
      result.push(line)
      line = rest
    }
  }
  if (line) result.push(line)
  return result
}

/**
 * useFamilyTree
 *
 * The two requests of the family tree view and the shapes they answer with
 * (``app.family_tree.router``):
 *
 * - ``fetchFamilyTree`` loads the tree the view's settings describe, for
 *   every point in time. Which part holds at an instant is worked out in
 *   ``familyTreeAt.ts``, so changing the instant needs no request.
 * - ``runFamilyTreeMapper`` derives the siblings of the people in the
 *   tree's scope and writes them; contradictions come back with the result.
 *
 * Instants are wall-clock minutes ``YYYY-MM-DDTHH:MM`` and compare as strings.
 *
 * Both send the family tree settings of the view as the user sees them
 * (``familyTreeSettings``); the server works with those rather than the
 * stored ones, so a change is drawn at once instead of after it was saved.
 */
import { apiClient } from '@/api/client'
import type { DatabaseView } from '@/stores/database'

export interface TreeSegment {
  start: string | null
  end: string | null
  nuance?: string | null
  base?: string | null
  label?: string | null
}

export type TreeToken =
  | { text: string }
  | { date: string; end: string | null }
  | { check: boolean }

export interface TreeSlot {
  start: string | null
  end: string | null
  tokens: TreeToken[]
}

export interface TreeProperty {
  schema_id: string
  name: string
  tokens?: TreeToken[] | null
  slots?: TreeSlot[] | null
}

export interface TreeNode {
  id: string
  title: string | null
  icon: string | null
  avatar: string | null
  generation: number | null
  outsider: boolean
  virtual: boolean
  birth: string | null
  death: string | null
  birth_value: string | null
  death_value: string | null
  sex: 'female' | 'male' | null
  warnings: string[]
  properties: TreeProperty[]
}

export interface ParentEdge {
  parent: string
  child: string
  anchor: boolean
  broken: boolean
  segments: TreeSegment[]
}

export interface PartnerEdge {
  a: string
  b: string
  segments: TreeSegment[]
}

export interface VirtualEdge {
  parent: string
  child: string
}

export interface OverlayEdge {
  source: string
  target: string
  segments: TreeSegment[]
}

export interface TreeOverlay {
  schema_id: string | null
  name: string | null
  valid: boolean
  direction: 'forward' | 'both' | 'none'
  color: string | null
  show_outsiders: boolean
  show_nuances: boolean
  edges: OverlayEdge[]
}

export interface FamilyTree {
  root: string | null
  root_valid: boolean
  scope: string
  nodes: TreeNode[]
  parent_edges: ParentEdge[]
  partner_edges: PartnerEdge[]
  virtual_edges: VirtualEdge[]
  overlays: TreeOverlay[]
}

export interface MapperFinding {
  kind: string
  entries: string[]
}

export interface MapperResult {
  added: number
  updated: number
  removed: number
  filled: number
  conflicts: MapperFinding[]
  warnings: MapperFinding[]
  entries: Record<string, { title: string | null; icon: string | null }>
}

function base(databaseId: string, viewId: string): string {
  return `/api/databases/${databaseId}/views/${viewId}/family-tree`
}

/** The family tree keys of *view*'s settings; unset ones are left out. */
export function familyTreeSettings(view: DatabaseView): Record<string, unknown> {
  const settings: Record<string, unknown> = {
    familyTreeRootId: view.familyTreeRootId,
    familyTreeAt: view.familyTreeAt,
    familyTreeScope: view.familyTreeScope,
    familyTreeProperties: view.familyTreeProperties,
    familyTreeOverlays: view.familyTreeOverlays,
  }
  return Object.fromEntries(Object.entries(settings).filter(([, value]) => value !== undefined && value !== null))
}

export function fetchFamilyTree(databaseId: string, view: DatabaseView): Promise<FamilyTree> {
  return apiClient.post<FamilyTree>(base(databaseId, view.id), { settings: familyTreeSettings(view) })
}

export function runFamilyTreeMapper(databaseId: string, view: DatabaseView): Promise<MapperResult> {
  return apiClient.post<MapperResult>(`${base(databaseId, view.id)}/map`, { settings: familyTreeSettings(view) })
}

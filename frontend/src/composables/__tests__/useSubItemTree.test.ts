import { describe, it, expect, beforeEach } from 'vitest'
import { ref } from 'vue'
import {
  childrenMapOf,
  flattenTree,
  parentMapOf,
  useSubItemTree,
} from '@/composables/useSubItemTree'
import type { DatabaseEntry, PropertySchema } from '@/stores/database'

const PARENT = 'schema-parent'
const SUB = 'schema-sub'

function schema(id: string, type: string): PropertySchema {
  return { id, database_id: 'db-a', name: id, type, config: {}, position: 0, group: 'Standard' }
}

function entry(id: string, parent?: string): DatabaseEntry {
  return {
    id,
    position: 0,
    content: { title: id },
    icon: null,
    state: 'active',
    values: parent ? { [PARENT]: { related_ids: [parent] } } : {},
  }
}

function rows(tree: { entry: DatabaseEntry; depth: number }[]): string[] {
  return tree.map(r => `${'-'.repeat(r.depth)}${r.entry.id}`)
}

// ── Pure helpers ──────────────────────────────────────────────────────────────

describe('parentMapOf / childrenMapOf', () => {
  it('maps each entry to its first parent and back', () => {
    const entries = [entry('a'), entry('b', 'a'), entry('c', 'a')]
    const parents = parentMapOf(entries, PARENT)
    expect([...parents]).toEqual([['b', 'a'], ['c', 'a']])
    expect([...childrenMapOf(parents)]).toEqual([['a', ['b', 'c']]])
  })
})

describe('flattenTree', () => {
  it('puts children right below their parent, indented, in the given order', () => {
    const entries = [entry('c', 'a'), entry('a'), entry('b'), entry('d', 'c')]
    const parents = parentMapOf(entries, PARENT)
    const tree = flattenTree(entries, parents, childrenMapOf(parents), new Set())
    expect(rows(tree)).toEqual(['a', '-c', '--d', 'b'])
  })

  it('treats an entry whose parent is not loaded as a root', () => {
    const entries = [entry('b', 'not-loaded')]
    const parents = parentMapOf(entries, PARENT)
    expect(rows(flattenTree(entries, parents, childrenMapOf(parents), new Set()))).toEqual(['b'])
  })

  it('leaves out the children of a folded entry', () => {
    const entries = [entry('a'), entry('b', 'a'), entry('c')]
    const parents = parentMapOf(entries, PARENT)
    expect(rows(flattenTree(entries, parents, childrenMapOf(parents), new Set(['a'])))).toEqual(['a', 'c'])
  })

  it('cuts a cycle instead of looping', () => {
    const entries = [entry('r'), entry('a', 'r'), entry('b', 'a')]
    const parents = parentMapOf(entries, PARENT)
    const children = childrenMapOf(parents)
    children.set('b', ['a'])
    expect(rows(flattenTree(entries, parents, children, new Set()))).toEqual(['r', '-a', '--b'])
  })
})

// ── Composable ────────────────────────────────────────────────────────────────

describe('useSubItemTree', () => {
  beforeEach(() => localStorage.clear())

  function setup(schemas: PropertySchema[], grouped = false) {
    const entries = ref([entry('a'), entry('b', 'a')])
    return useSubItemTree({
      schemas: ref(schemas),
      entries,
      isGrouped: ref(grouped),
      storageKey: 'fold-test',
    })
  }

  it('renders a tree while the sub-item pair exists', () => {
    const tree = setup([schema(PARENT, 'parent_item'), schema(SUB, 'sub_item')])
    expect(rows(tree.flatTreeEntries.value)).toEqual(['a', '-b'])
    expect(tree.subItemChildrenMap.value.get('a')).toEqual(['b'])
  })

  it('renders flat without the sub_item schema or when grouped', () => {
    expect(rows(setup([schema(PARENT, 'parent_item')]).flatTreeEntries.value)).toEqual(['a', 'b'])
    const grouped = setup([schema(PARENT, 'parent_item'), schema(SUB, 'sub_item')], true)
    expect(rows(grouped.flatTreeEntries.value)).toEqual(['a', 'b'])
  })

  it('folds and keeps the fold state across instances', () => {
    const schemas = [schema(PARENT, 'parent_item'), schema(SUB, 'sub_item')]
    const tree = setup(schemas)
    tree.toggleFold('a')
    expect(rows(tree.flatTreeEntries.value)).toEqual(['a'])

    const again = setup(schemas)
    again.loadFoldState()
    expect(again.foldedEntries.value.has('a')).toBe(true)
    again.toggleFold('a')
    expect(rows(again.flatTreeEntries.value)).toEqual(['a', '-b'])
  })
})

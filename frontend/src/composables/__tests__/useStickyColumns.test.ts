import { describe, it, expect } from 'vitest'
import { ref } from 'vue'
import { frozenOffsetsFrom, useStickyColumns } from '@/composables/useStickyColumns'
import type { DatabaseView } from '@/stores/database'

describe('frozenOffsetsFrom', () => {
  it('places each frozen column after the handle and the columns before it', () => {
    expect(frozenOffsetsFrom(24, [160, 120, 80], ['a', 'b', 'c'], 2)).toEqual({ a: 24, b: 184 })
  })

  it('freezes no more columns than exist', () => {
    expect(frozenOffsetsFrom(24, [160], ['a'], 3)).toEqual({ a: 24 })
  })
})

describe('useStickyColumns', () => {
  function view(frozenColumns: number): DatabaseView {
    return {
      id: 'view-a', name: 'View A', viewType: 'table', colOrder: [], colWidths: {},
      filterGroups: [], sorts: [], hiddenColumns: [], frozenColumns,
    } as DatabaseView
  }

  it('pins the handle column only while a column is frozen', () => {
    const activeView = ref<DatabaseView | null>(view(0))
    const sticky = useStickyColumns({
      activeView,
      activeViewId: ref('view-a'),
      orderedColumns: ref([{ key: 'a' }, { key: 'b' }]),
    })
    expect(sticky.handleFrozen.value).toBe(false)
    expect(sticky.handleFrozenStyle.value).toEqual({})
    activeView.value = view(1)
    expect(sticky.handleFrozen.value).toBe(true)
    expect(sticky.handleFrozenStyle.value).toEqual({ left: '0px' })
    // Not measured yet: the column is frozen but has no offset to apply.
    expect(sticky.isFrozenCol('a')).toBe(false)
    expect(sticky.frozenColStyle('b')).toEqual({})
  })
})

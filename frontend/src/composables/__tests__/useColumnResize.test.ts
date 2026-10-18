import { describe, it, expect, vi } from 'vitest'
import { ref } from 'vue'
import type { DatabaseView } from '@/stores/database'
import {
  DEFAULT_COLUMN_WIDTH,
  MIN_COLUMN_WIDTH,
  useColumnResize,
} from '@/composables/useColumnResize'

function setup(colWidths?: Record<string, number>) {
  const view = { id: 'view-a', colWidths } as unknown as DatabaseView
  const views = ref<DatabaseView[]>([view])
  const saveViews = vi.fn(async () => {})
  const resize = useColumnResize({ views, activeViewId: ref('view-a'), saveViews })
  resize.syncColWidthsFromView(view)
  return { resize, view, saveViews }
}

describe('colStyle', () => {
  it('uses the stored width of a column', () => {
    const { resize } = setup({ 'col-a': 240 })
    expect(resize.colStyle('col-a')).toEqual({ width: '240px', minWidth: '240px' })
  })

  it('gives a column without a stored width the default width', () => {
    const { resize } = setup({})
    expect(resize.colStyle('col-b')).toEqual({
      width: `${DEFAULT_COLUMN_WIDTH}px`,
      minWidth: `${DEFAULT_COLUMN_WIDTH}px`,
    })
  })

  it('defaults to the minimum width of the name column', () => {
    expect(DEFAULT_COLUMN_WIDTH).toBe(160)
  })

  it('treats a view without any widths like one without this column', () => {
    const { resize } = setup(undefined)
    expect(resize.colStyle('col-a').width).toBe(`${DEFAULT_COLUMN_WIDTH}px`)
  })
})

describe('resizing', () => {
  function pointer(clientX: number, target: HTMLElement): PointerEvent {
    const event = new Event('pointerdown') as PointerEvent
    Object.defineProperties(event, {
      clientX: { value: clientX },
      pointerId: { value: 1 },
      currentTarget: { value: target },
    })
    return event
  }

  function handle(): HTMLElement {
    const el = document.createElement('div')
    el.setPointerCapture = vi.fn()
    return el
  }

  it('starts an unsized column from the default width and saves the result', async () => {
    const { resize, view, saveViews } = setup({})
    const el = handle()
    resize.startResize(pointer(100, el), 'col-a', null)

    const move = new Event('pointermove') as PointerEvent
    Object.defineProperty(move, 'clientX', { value: 140 })
    el.dispatchEvent(move)
    el.dispatchEvent(new Event('pointerup'))
    await Promise.resolve()

    expect(resize.colWidths.value['col-a']).toBe(DEFAULT_COLUMN_WIDTH + 40)
    expect(view.colWidths).toEqual({ 'col-a': DEFAULT_COLUMN_WIDTH + 40 })
    expect(saveViews).toHaveBeenCalledOnce()
  })

  it('never narrows a column below the minimum width', () => {
    const { resize } = setup({ 'col-a': 120 })
    const el = handle()
    resize.startResize(pointer(300, el), 'col-a', null)

    const move = new Event('pointermove') as PointerEvent
    Object.defineProperty(move, 'clientX', { value: 0 })
    el.dispatchEvent(move)

    expect(resize.colWidths.value['col-a']).toBe(MIN_COLUMN_WIDTH)
  })
})

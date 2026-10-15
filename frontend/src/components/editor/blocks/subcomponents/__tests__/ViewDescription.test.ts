import { describe, it, expect, afterEach, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key }),
}))

import ViewDescription from '../ViewDescription.vue'

// Mounted with a bare createApp: the component needs no plugins once i18n is
// mocked, and this keeps the test free of extra dependencies.

let app: App | null = null
let host: HTMLElement | null = null

function mount(description: string) {
  const onSave = vi.fn()
  host = document.createElement('div')
  document.body.appendChild(host)
  app = createApp({
    render: () => h(ViewDescription, { viewId: 'v1', description, onSave }),
  })
  app.mount(host)
  return { root: host, onSave }
}

afterEach(() => {
  app?.unmount()
  host?.remove()
  app = null
  host = null
})

async function openEditor(root: HTMLElement): Promise<HTMLTextAreaElement> {
  root.querySelector<HTMLElement>('.vd__display')!.click()
  await nextTick()
  await nextTick()
  return root.querySelector<HTMLTextAreaElement>('textarea')!
}

async function type(el: HTMLTextAreaElement, text: string): Promise<void> {
  el.value = text
  el.dispatchEvent(new Event('input'))
  await nextTick()
}

describe('ViewDescription', () => {
  it('shows the description text without a placeholder', () => {
    const { root } = mount('Open tasks')
    const display = root.querySelector('.vd__display')!
    expect(display.textContent).toBe('Open tasks')
    expect(display.classList.contains('vd__display--empty')).toBe(false)
  })

  it('renders an empty description as a blank strip', () => {
    const { root } = mount('')
    const display = root.querySelector('.vd__display')!
    expect(display.textContent).toBe('')
    expect(display.classList.contains('vd__display--empty')).toBe(true)
    expect(root.querySelector('[placeholder]')).toBeNull()
  })

  it('opens an editable field on click, prefilled with the description', async () => {
    const { root } = mount('Open tasks')
    const textarea = await openEditor(root)
    expect(textarea).not.toBeNull()
    expect(textarea.value).toBe('Open tasks')
  })

  it('saves on click-away (blur) with the view id', async () => {
    const { root, onSave } = mount('')
    const textarea = await openEditor(root)
    await type(textarea, 'New text')
    textarea.dispatchEvent(new Event('blur'))
    await nextTick()
    expect(onSave).toHaveBeenCalledWith('v1', 'New text')
    expect(root.querySelector('textarea')).toBeNull()
  })

  it('does not save when the text is unchanged', async () => {
    const { root, onSave } = mount('Same')
    const textarea = await openEditor(root)
    textarea.dispatchEvent(new Event('blur'))
    await nextTick()
    expect(onSave).not.toHaveBeenCalled()
  })

  it('discards the edit on Escape', async () => {
    const { root, onSave } = mount('Original')
    const textarea = await openEditor(root)
    await type(textarea, 'Changed')
    textarea.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await nextTick()
    expect(onSave).not.toHaveBeenCalled()
    expect(root.querySelector('textarea')).toBeNull()
    expect(root.querySelector('.vd__display')!.textContent).toBe('Original')
  })

  it('reports an emptied description so the caller can remove it', async () => {
    const { root, onSave } = mount('Something')
    const textarea = await openEditor(root)
    await type(textarea, '')
    textarea.dispatchEvent(new Event('blur'))
    await nextTick()
    expect(onSave).toHaveBeenCalledWith('v1', '')
  })
})

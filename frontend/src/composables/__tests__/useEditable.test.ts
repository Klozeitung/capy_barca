import { describe, it, expect, afterEach } from 'vitest'
import { createApp, defineComponent, h, nextTick, ref, type App, type ComputedRef, type Ref } from 'vue'
import { canWrite, provideEditable, useEditable, type WriteAccess } from '@/composables/useEditable'

// ── Helpers ───────────────────────────────────────────────────────────────────

let app: App | null = null

afterEach(() => {
  app?.unmount()
  app = null
})

/**
 * Mount a chain of providers, outermost first, with a reader at the bottom.
 * Returns the editable state the reader sees and the states the providers
 * return to themselves.
 */
function mountChain(blocks: Ref<WriteAccess | null | undefined>[]): {
  reader: ComputedRef<boolean>
  providers: ComputedRef<boolean>[]
} {
  let reader!: ComputedRef<boolean>
  const providers: ComputedRef<boolean>[] = []

  const Reader = defineComponent({
    setup() {
      reader = useEditable()
      return () => h('span')
    },
  })

  let child: ReturnType<typeof defineComponent> = Reader
  for (const block of [...blocks].reverse()) {
    const inner = child
    child = defineComponent({
      setup() {
        providers.unshift(provideEditable(block))
        return () => h(inner)
      },
    })
  }

  app = createApp(child)
  app.mount(document.createElement('div'))
  return { reader, providers }
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('canWrite', () => {
  it('is false only when the block says so', () => {
    expect(canWrite({ can_write: false })).toBe(false)
    expect(canWrite({ can_write: true })).toBe(true)
    expect(canWrite({})).toBe(true)
    expect(canWrite(null)).toBe(true)
    expect(canWrite(undefined)).toBe(true)
  })
})

describe('useEditable', () => {
  it('is editable without any provider', () => {
    const { reader } = mountChain([])
    expect(reader.value).toBe(true)
  })

  it('reads the nearest provider', () => {
    const { reader } = mountChain([ref({ can_write: true }), ref({ can_write: false })])
    expect(reader.value).toBe(false)
  })

  it('lets an inner block grant what an outer one does not', () => {
    // A writable database shown on a page the user may only read.
    const { reader } = mountChain([ref({ can_write: false }), ref({ can_write: true })])
    expect(reader.value).toBe(true)
  })

  it('passes the outer state on when the block has no value', () => {
    const { reader, providers } = mountChain([ref({ can_write: false }), ref({})])
    expect(providers[1].value).toBe(false)
    expect(reader.value).toBe(false)
  })

  it('passes the outer state on when there is no block yet', () => {
    const { reader } = mountChain([ref({ can_write: false }), ref(null)])
    expect(reader.value).toBe(false)
  })

  it('follows changes of the block', async () => {
    const block = ref<WriteAccess>({ can_write: true })
    const { reader, providers } = mountChain([block])
    expect(reader.value).toBe(true)

    block.value = { can_write: false }
    await nextTick()

    expect(providers[0].value).toBe(false)
    expect(reader.value).toBe(false)
  })
})

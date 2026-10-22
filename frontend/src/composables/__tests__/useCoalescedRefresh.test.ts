import { describe, it, expect, vi } from 'vitest'
import { useCoalescedRefresh, type RefreshKind } from '@/composables/useCoalescedRefresh'

function deferred() {
  let resolve: () => void = () => {}
  let reject: (reason: unknown) => void = () => {}
  const promise = new Promise<void>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

/** A refresh whose runs are recorded and finished by hand. */
function controlledRuns() {
  const kinds: RefreshKind[] = []
  const runs: ReturnType<typeof deferred>[] = []
  const run = vi.fn((kind: RefreshKind) => {
    kinds.push(kind)
    const d = deferred()
    runs.push(d)
    return d.promise
  })
  return { kinds, runs, refresh: useCoalescedRefresh(run) }
}

const settle = () => new Promise((r) => setTimeout(r, 0))

describe('useCoalescedRefresh', () => {
  it('runs a request right away when idle', async () => {
    const { kinds, runs, refresh } = controlledRuns()

    const done = refresh.request('sync')
    await settle()
    expect(kinds).toEqual(['sync'])

    runs[0].resolve()
    await done
    expect(refresh.isBusy()).toBe(false)
  })

  it('merges requests made during a run into one further run', async () => {
    const { kinds, runs, refresh } = controlledRuns()

    const first = refresh.request('sync')
    await settle()
    const second = refresh.request('sync')
    const third = refresh.request('sync')
    expect(second).toBe(third)
    expect(kinds).toEqual(['sync'])

    runs[0].resolve()
    await first
    await settle()
    expect(kinds).toEqual(['sync', 'sync'])

    runs[1].resolve()
    await second
    expect(refresh.isBusy()).toBe(false)
  })

  it('a request made during a run is answered only by the next run', async () => {
    const { runs, refresh } = controlledRuns()
    refresh.request('sync')
    await settle()

    let answered = false
    refresh.request('sync').then(() => { answered = true })
    runs[0].resolve()
    await settle()
    expect(answered).toBe(false)

    runs[1].resolve()
    await settle()
    expect(answered).toBe(true)
  })

  it('a pending run becomes full when any merged request asks for it', async () => {
    const { kinds, runs, refresh } = controlledRuns()
    refresh.request('sync')
    await settle()

    refresh.request('sync')
    refresh.request('full')
    refresh.request('sync')
    runs[0].resolve()
    await settle()

    expect(kinds).toEqual(['sync', 'full'])
    runs[1].resolve()
  })

  it('a failed run rejects its callers and does not stop the next one', async () => {
    const { kinds, runs, refresh } = controlledRuns()

    const failing = refresh.request('sync')
    await settle()
    const next = refresh.request('full')
    runs[0].reject(new Error('offline'))

    await expect(failing).rejects.toThrow('offline')
    await settle()
    expect(kinds).toEqual(['sync', 'full'])
    runs[1].resolve()
    await expect(next).resolves.toBeUndefined()
  })

  it('a failure of the merged run reaches the requests it answers', async () => {
    const { runs, refresh } = controlledRuns()
    const first = refresh.request('sync')
    await settle()
    const merged = refresh.request('sync')

    runs[0].resolve()
    await first
    await settle()
    runs[1].reject(new Error('refused'))

    await expect(merged).rejects.toThrow('refused')
    expect(refresh.isBusy()).toBe(false)
  })
})

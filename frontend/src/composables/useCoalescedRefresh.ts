/**
 * useCoalescedRefresh
 *
 * Runs the refreshes of one view one at a time and merges those that pile up.
 *
 * Several changes often arrive in quick succession: creating and linking an
 * entry sends three events, and the client that made a change also asks for a
 * refresh itself. Each of them used to start a load of its own. Here at most
 * one refresh runs; every request made while it runs is answered by a single
 * further run that starts once it has finished, so the result reflects all of
 * them. A request made during a run is never answered by that run, because
 * the run may have read the server's state before the change it reports.
 *
 * There are two kinds. ``sync`` compares the entries shown with the server's
 * and loads only what is missing; ``full`` loads everything shown again. A
 * pending run becomes ``full`` as soon as one of the requests it answers asks
 * for that.
 *
 * Usage:
 *   const refresh = useCoalescedRefresh((kind) => kind === 'full' ? reload() : sync())
 *   await refresh.request('sync')
 */

/** ``sync`` loads only what changed; ``full`` loads everything again. */
export type RefreshKind = 'sync' | 'full'

interface PendingRun {
  kind: RefreshKind
  promise: Promise<void>
  resolve: () => void
  reject: (reason: unknown) => void
}

export function useCoalescedRefresh(run: (kind: RefreshKind) => Promise<void>) {
  let running: Promise<void> | null = null
  let pending: PendingRun | null = null

  function start(kind: RefreshKind): Promise<void> {
    const current = Promise.resolve().then(() => run(kind))
    running = current
    const next = () => {
      running = null
      const queued = pending
      if (!queued) return
      pending = null
      start(queued.kind).then(queued.resolve, queued.reject)
    }
    current.then(next, next)
    return current
  }

  /**
   * Ask for a refresh of *kind*. The promise settles when a run that started
   * after this request has finished, and rejects when that run fails.
   */
  function request(kind: RefreshKind): Promise<void> {
    if (!running) return start(kind)
    if (pending) {
      if (kind === 'full') pending.kind = 'full'
      return pending.promise
    }
    let resolve!: () => void
    let reject!: (reason: unknown) => void
    const promise = new Promise<void>((res, rej) => { resolve = res; reject = rej })
    pending = { kind, promise, resolve, reject }
    return promise
  }

  /** Whether a refresh is running or waiting to run. */
  function isBusy(): boolean {
    return running !== null || pending !== null
  }

  return { request, isBusy }
}

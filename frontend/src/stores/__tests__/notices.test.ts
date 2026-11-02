import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useNoticesStore, NOTICE_DURATION_MS, MAX_NOTICES } from '@/stores/notices'

describe('useNoticesStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('shows a notice and closes it after its time', () => {
    const store = useNoticesStore()
    store.push('notices.valueOverwritten', { user: 'person-b' })
    expect(store.notices).toHaveLength(1)

    vi.advanceTimersByTime(NOTICE_DURATION_MS)

    expect(store.notices).toHaveLength(0)
  })

  it('a repeated notice is shown once and its time starts over', () => {
    const store = useNoticesStore()
    const first = store.push('notices.valueOverwritten', { user: 'person-b' })
    vi.advanceTimersByTime(NOTICE_DURATION_MS - 1)
    const second = store.push('notices.valueOverwritten', { user: 'person-b' })
    vi.advanceTimersByTime(NOTICE_DURATION_MS - 1)

    expect(second).toBe(first)
    expect(store.notices).toHaveLength(1)
  })

  it('keeps only the newest notices', () => {
    const store = useNoticesStore()
    for (let i = 0; i <= MAX_NOTICES; i++) store.push('notices.valueOverwritten', { user: `person-${i}` })

    expect(store.notices).toHaveLength(MAX_NOTICES)
    expect(store.notices[0].params.user).toBe('person-1')
  })

  it('dismiss closes a notice at once', () => {
    const store = useNoticesStore()
    const id = store.push('notices.valueOverwritten')
    store.dismiss(id)
    expect(store.notices).toHaveLength(0)
  })

  it('a sticky notice stays and does not count against the limit', () => {
    const store = useNoticesStore()
    store.push('alerts.notice', { entry: 'Entry A' }, { sticky: true, tag: 'alert:a' })
    for (let i = 0; i < MAX_NOTICES; i++) store.push('notices.valueOverwritten', { user: `person-${i}` })

    vi.advanceTimersByTime(NOTICE_DURATION_MS * 10)

    expect(store.notices.map((n) => n.tag)).toEqual(['alert:a'])
  })

  it('a notice with the same tag is shown once', () => {
    const store = useNoticesStore()
    const first = store.push('alerts.notice', { entry: 'Entry A' }, { sticky: true, tag: 'alert:a' })
    const second = store.push('alerts.notice', { entry: 'Entry A' }, { sticky: true, tag: 'alert:a' })
    expect(second).toBe(first)
    expect(store.notices).toHaveLength(1)
  })

  it('dismiss runs onClose, dismissTag does not', () => {
    const store = useNoticesStore()
    const closed: string[] = []
    const a = store.push('alerts.notice', {}, { sticky: true, tag: 'alert:a', onClose: () => closed.push('a') })
    store.push('alerts.notice', {}, { sticky: true, tag: 'alert:b', onClose: () => closed.push('b') })

    store.dismiss(a)
    store.dismissTag('alert:b')

    expect(closed).toEqual(['a'])
    expect(store.notices).toHaveLength(0)
  })

  it('open runs onOpen and closes the notice', () => {
    const store = useNoticesStore()
    const calls: string[] = []
    const id = store.push('alerts.notice', {}, {
      sticky: true, tag: 'alert:a',
      onOpen: () => calls.push('open'), onClose: () => calls.push('close'),
    })

    store.open(id)

    expect(calls).toEqual(['open', 'close'])
    expect(store.notices).toHaveLength(0)
  })

  it('open does nothing for a notice that cannot be opened', () => {
    const store = useNoticesStore()
    const id = store.push('notices.readOnly')
    store.open(id)
    expect(store.notices).toHaveLength(1)
  })
})

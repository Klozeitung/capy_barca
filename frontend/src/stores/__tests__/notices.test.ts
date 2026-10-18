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
})

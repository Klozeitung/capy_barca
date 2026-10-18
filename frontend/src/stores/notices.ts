import { ref } from 'vue'
import { defineStore } from 'pinia'

/** How long a notice stays before it closes on its own. */
export const NOTICE_DURATION_MS = 8000

/** Notices shown at once; the oldest goes when another arrives. */
export const MAX_NOTICES = 4

/**
 * A short message for the user, rendered by ``NoticeToasts``.
 *
 * ``key`` is an i18n key and ``params`` its named parameters, so the text is
 * translated where it is shown and follows a change of language.
 */
export interface Notice {
  id: number
  key: string
  params: Record<string, string>
}

/**
 * Transient notices, such as the one telling a user that someone else
 * changed a value they had just edited.
 *
 * The same notice (same key and parameters) is not shown twice at once; a
 * repeat restarts its timer instead.
 */
export const useNoticesStore = defineStore('notices', () => {
  const notices = ref<Notice[]>([])
  const _timers = new Map<number, ReturnType<typeof setTimeout>>()
  let _nextId = 1

  function _arm(id: number): void {
    const running = _timers.get(id)
    if (running !== undefined) clearTimeout(running)
    _timers.set(id, setTimeout(() => dismiss(id), NOTICE_DURATION_MS))
  }

  function push(key: string, params: Record<string, string> = {}): number {
    const same = notices.value.find(
      (n) => n.key === key && JSON.stringify(n.params) === JSON.stringify(params),
    )
    if (same) {
      _arm(same.id)
      return same.id
    }
    const notice: Notice = { id: _nextId++, key, params }
    notices.value.push(notice)
    while (notices.value.length > MAX_NOTICES) dismiss(notices.value[0].id)
    _arm(notice.id)
    return notice.id
  }

  function dismiss(id: number): void {
    const timer = _timers.get(id)
    if (timer !== undefined) clearTimeout(timer)
    _timers.delete(id)
    notices.value = notices.value.filter((n) => n.id !== id)
  }

  return { notices, push, dismiss }
})

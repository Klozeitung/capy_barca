import { ref } from 'vue'
import { defineStore } from 'pinia'

/** How long a notice stays before it closes on its own. */
export const NOTICE_DURATION_MS = 8000

/** Notices that close on their own shown at once; the oldest goes when another arrives. */
export const MAX_NOTICES = 4

/**
 * A short message for the user, rendered by ``NoticeToasts``.
 *
 * ``key`` is an i18n key and ``params`` its named parameters, so the text is
 * translated where it is shown and follows a change of language.
 *
 * A ``sticky`` notice stays until the user closes it. ``tag`` names what the
 * notice is about, so it can be removed when that goes away elsewhere
 * (``dismissTag``). ``onOpen`` makes the text clickable; ``onClose`` runs
 * when the user closes the notice, by its button or by opening it.
 */
export interface Notice {
  id: number
  key: string
  params: Record<string, string>
  sticky?: boolean
  tag?: string
  icon?: string
  onOpen?: () => void
  onClose?: () => void
}

/** Options of ``push``. */
export interface NoticeOptions {
  sticky?: boolean
  tag?: string
  icon?: string
  onOpen?: () => void
  onClose?: () => void
}

/**
 * Notices, such as the one telling a user that someone else changed a value
 * they had just edited, or an alert that fell due.
 *
 * The same notice (same key and parameters, or the same tag) is not shown
 * twice at once; a repeat restarts its timer instead. Sticky notices have no
 * timer and do not count against ``MAX_NOTICES``.
 */
export const useNoticesStore = defineStore('notices', () => {
  const notices = ref<Notice[]>([])
  const _timers = new Map<number, ReturnType<typeof setTimeout>>()
  let _nextId = 1

  function _arm(id: number): void {
    const running = _timers.get(id)
    if (running !== undefined) clearTimeout(running)
    _timers.set(id, setTimeout(() => _remove(id), NOTICE_DURATION_MS))
  }

  function push(
    key: string,
    params: Record<string, string> = {},
    options: NoticeOptions = {},
  ): number {
    const same = notices.value.find((n) =>
      options.tag !== undefined
        ? n.tag === options.tag
        : n.tag === undefined && n.key === key && JSON.stringify(n.params) === JSON.stringify(params),
    )
    if (same) {
      if (!same.sticky) _arm(same.id)
      return same.id
    }
    const notice: Notice = { id: _nextId++, key, params, ...options }
    notices.value.push(notice)
    if (!notice.sticky) {
      let transient = notices.value.filter((n) => !n.sticky)
      while (transient.length > MAX_NOTICES) {
        _remove(transient[0].id)
        transient = notices.value.filter((n) => !n.sticky)
      }
      _arm(notice.id)
    }
    return notice.id
  }

  function _remove(id: number): Notice | undefined {
    const timer = _timers.get(id)
    if (timer !== undefined) clearTimeout(timer)
    _timers.delete(id)
    const notice = notices.value.find((n) => n.id === id)
    notices.value = notices.value.filter((n) => n.id !== id)
    return notice
  }

  /** Close a notice the user dismissed; runs its ``onClose``. */
  function dismiss(id: number): void {
    _remove(id)?.onClose?.()
  }

  /** Open a notice: runs its ``onOpen`` and closes it. */
  function open(id: number): void {
    const notice = notices.value.find((n) => n.id === id)
    if (!notice?.onOpen) return
    notice.onOpen()
    dismiss(id)
  }

  /** Remove the notices tagged *tag* without running their ``onClose``. */
  function dismissTag(tag: string): void {
    for (const notice of notices.value.filter((n) => n.tag === tag)) _remove(notice.id)
  }

  return { notices, push, dismiss, open, dismissTag }
})

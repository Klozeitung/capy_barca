/**
 * useMentionMenu
 *
 * Per-component composable for the @ mention picker.
 * Mirrors the structure of useSlashMenu but operates on a dynamic list
 * of pages / databases sourced from the block store at open time.
 */
import { ref, computed } from 'vue'
import type { Block } from '@/stores/blocks'

export function useMentionMenu() {
  const isOpen   = ref(false)
  const query    = ref('')
  const activeIndex = ref(0)
  const anchorRect  = ref<DOMRect | null>(null)
  const _pages  = ref<Block[]>([])

  const filteredPages = computed<Block[]>(() => {
    const q = query.value.trim().toLowerCase()
    const list = q
      ? _pages.value.filter((p) => {
          const title = (p.content?.title as string | undefined) ?? ''
          return title.toLowerCase().includes(q) || p.type.includes(q)
        })
      : _pages.value
    return list.slice(0, 12)
  })

  function open(rect: DOMRect, pages: Block[]): void {
    _pages.value   = pages
    anchorRect.value = rect
    activeIndex.value = 0
    query.value      = ''
    isOpen.value     = true
  }

  function close(): void {
    isOpen.value      = false
    query.value       = ''
    activeIndex.value = 0
    anchorRect.value  = null
  }

  function updateQuery(q: string): void {
    query.value = q
    const max = Math.max(0, filteredPages.value.length - 1)
    activeIndex.value = Math.min(activeIndex.value, max)
  }

  function navigate(direction: 'up' | 'down'): void {
    const count = filteredPages.value.length
    if (!count) return
    activeIndex.value = direction === 'down'
      ? (activeIndex.value + 1) % count
      : (activeIndex.value - 1 + count) % count
  }

  function setActiveIndex(idx: number): void {
    activeIndex.value = idx
  }

  function getActivePage(): Block | null {
    return filteredPages.value[activeIndex.value] ?? null
  }

  return {
    isOpen,
    query,
    activeIndex,
    anchorRect,
    filteredPages,
    open,
    close,
    updateQuery,
    navigate,
    setActiveIndex,
    getActivePage,
  }
}

/**
 * useToggleState
 *
 * Provides a module-level singleton map of toggle open states shared across
 * all BlockContentSection instances and TableOfContentsBlock.
 *
 * Keeping this outside individual component instances allows the TOC to
 * programmatically open collapsed ancestor toggles when navigating to a
 * heading that is currently hidden inside a folded section.
 *
 * Toggle state is intentionally session-only and is not persisted to the
 * backend. All regular `toggle`, `text_toggle`, and heading toggle block types
 * use this map.
 */
import { ref } from 'vue'
import type { Ref } from 'vue'

const _toggleOpenStates: Ref<Record<string, boolean>> = ref({})

export function useToggleState(): { toggleOpenStates: Ref<Record<string, boolean>> } {
  return { toggleOpenStates: _toggleOpenStates }
}

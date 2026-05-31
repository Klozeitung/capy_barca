<script setup lang="ts">
import { computed, watch, ref, nextTick } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import type { SlashMenuItem } from '@/composables/useSlashMenu'

const props = defineProps<{
  show: boolean
  items: SlashMenuItem[]
  activeIndex: number
  anchorRect: DOMRect | null
}>()

const emit = defineEmits<{
  (e: 'select', type: string): void
  (e: 'set-active', index: number): void
  (e: 'close'): void
}>()

const { t } = useI18n()
const menuEl = ref<HTMLElement | null>(null)

const MENU_WIDTH = 240

/** Fixed position style derived from the anchor element's bounding rect. */
const style = computed(() => {
  const rect = props.anchorRect
  if (!rect) return {}

  const viewportWidth = window.innerWidth
  const viewportHeight = window.innerHeight
  const menuHeight = Math.min(props.items.length * 56, 280)

  // Horizontal: clamp so menu doesn't overflow right edge.
  let left = rect.left
  if (left + MENU_WIDTH > viewportWidth - 8) {
    left = Math.max(8, viewportWidth - MENU_WIDTH - 8)
  }

  // Vertical: prefer below, flip above when not enough space.
  const spaceBelow = viewportHeight - rect.bottom - 8
  const top =
    spaceBelow >= menuHeight
      ? rect.bottom + 4
      : Math.max(8, rect.top - menuHeight - 4)

  return {
    top: `${top}px`,
    left: `${left}px`,
    width: `${MENU_WIDTH}px`,
  }
})

// Scroll the active item into view whenever the index changes.
watch(
  () => props.activeIndex,
  async () => {
    await nextTick()
    const el = menuEl.value?.querySelector<HTMLElement>('.slash-menu__item--active')
    el?.scrollIntoView({ block: 'nearest' })
  },
)

/**
 * Determine whether a group label should be rendered above item at index i.
 * A label is shown when the item has a ``group`` key AND either it is the
 * first item in the filtered list OR the previous item belongs to a different
 * group (or has no group).
 */
function showGroupLabel(idx: number): boolean {
  const item = props.items[idx]
  if (!item.group) return false
  if (idx === 0) return true
  return props.items[idx - 1].group !== item.group
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="show && items.length > 0"
      ref="menuEl"
      class="slash-menu"
      :style="style"
      role="listbox"
      aria-label="Block-Typ auswählen"
    >
      <template v-for="(item, idx) in items" :key="item.type">
        <!-- Group separator label -->
        <div v-if="showGroupLabel(idx)" class="slash-menu__group-label">
          {{ t(item.group!) }}
        </div>

        <button
          class="slash-menu__item"
          :class="{ 'slash-menu__item--active': idx === activeIndex }"
          role="option"
          :aria-selected="idx === activeIndex"
          tabindex="-1"
          @mousedown.prevent="emit('select', item.type)"
          @mousemove="emit('set-active', idx)"
        >
          <span class="slash-menu__icon">
            <Icon :icon="item.icon" width="18" height="18" />
          </span>
          <span class="slash-menu__text">
            <span class="slash-menu__label">{{ t(item.labelKey) }}</span>
            <span class="slash-menu__desc">{{ t(item.descKey) }}</span>
          </span>
        </button>
      </template>

      <div v-if="items.length === 0" class="slash-menu__empty">
        Keine Ergebnisse
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.slash-menu {
  position: fixed;
  z-index: 1000;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
  padding: 4px;
  max-height: 320px;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: var(--color-border) transparent;
}

/* ── Group label ─────────────────────────────────────────────────────────── */
.slash-menu__group-label {
  padding: 8px 8px 3px;
  font-size: 0.65rem;
  font-weight: 700;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  color: var(--color-text-muted);
  user-select: none;
}

/* Add a top border to every group label except the very first one. */
.slash-menu__group-label:not(:first-child) {
  margin-top: 4px;
  padding-top: 10px;
  border-top: 1px solid var(--color-border);
}

/* ── Items ───────────────────────────────────────────────────────────────── */
.slash-menu__item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 7px 8px;
  border: none;
  border-radius: 6px;
  background: none;
  cursor: pointer;
  text-align: left;
  transition: background 0.1s;
  color: var(--color-text);
}

.slash-menu__item:hover,
.slash-menu__item--active {
  background: var(--color-hover);
}

.slash-menu__item--active {
  background: var(--color-active);
}

.slash-menu__icon {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 6px;
  color: var(--color-text-muted);
}

.slash-menu__item--active .slash-menu__icon {
  color: var(--color-accent);
  border-color: var(--color-accent);
}

.slash-menu__text {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.slash-menu__label {
  font-size: 0.875rem;
  font-weight: 500;
  color: var(--color-text);
  white-space: nowrap;
}

.slash-menu__desc {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.slash-menu__empty {
  padding: 10px 12px;
  font-size: 0.875rem;
  color: var(--color-text-muted);
}
</style>

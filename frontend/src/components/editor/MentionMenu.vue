<script setup lang="ts">
import { computed, watch, ref, nextTick } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import type { Block } from '@/stores/blocks'

const props = defineProps<{
  show: boolean
  pages: Block[]
  activeIndex: number
  anchorRect: DOMRect | null
}>()

const emit = defineEmits<{
  (e: 'select', block: Block): void
  (e: 'set-active', index: number): void
  (e: 'close'): void
}>()

const { t } = useI18n()
const menuEl = ref<HTMLElement | null>(null)
const MENU_WIDTH = 240

const style = computed(() => {
  const rect = props.anchorRect
  if (!rect) return {}

  const vw = window.innerWidth
  const vh = window.innerHeight
  const menuHeight = Math.min(props.pages.length * 44 + 8, 260)

  let left = rect.left
  if (left + MENU_WIDTH > vw - 8) left = Math.max(8, vw - MENU_WIDTH - 8)

  const spaceBelow = vh - rect.bottom - 8
  const top = spaceBelow >= menuHeight
    ? rect.bottom + 4
    : Math.max(8, rect.top - menuHeight - 4)

  return { top: `${top}px`, left: `${left}px`, width: `${MENU_WIDTH}px` }
})

watch(
  () => props.activeIndex,
  async () => {
    await nextTick()
    menuEl.value?.querySelector<HTMLElement>('.mention-menu__item--active')?.scrollIntoView({ block: 'nearest' })
  },
)

function blockIcon(block: Block): string {
  if (block.icon) return block.icon
  return block.type === 'database' ? 'mdi:table' : 'mdi:file-document-outline'
}

function blockTitle(block: Block): string {
  return (block.content?.title as string | undefined) || t('nav.untitled')
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="show"
      ref="menuEl"
      class="mention-menu"
      :style="style"
      role="listbox"
    >
      <template v-if="pages.length > 0">
        <button
          v-for="(page, idx) in pages"
          :key="page.id"
          class="mention-menu__item"
          :class="{ 'mention-menu__item--active': idx === activeIndex }"
          role="option"
          :aria-selected="idx === activeIndex"
          tabindex="-1"
          @pointerdown.prevent="() => { console.debug('[MentionMenu] pointerdown on', page.id); emit('select', page) }"
          @mousemove="emit('set-active', idx)"
        >
          <span class="mention-menu__icon">
            <Icon :icon="blockIcon(page)" width="15" height="15" />
          </span>
          <span class="mention-menu__title">{{ blockTitle(page) }}</span>
          <span class="mention-menu__type">{{ t(`block.types.${page.type}`) }}</span>
        </button>
      </template>
      <div v-else class="mention-menu__empty">
        {{ t('editor.mention.noResults') }}
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.mention-menu {
  position: fixed;
  z-index: 1000;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
  padding: 4px;
  max-height: 260px;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: var(--color-border) transparent;
}

.mention-menu__item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 6px 8px;
  border: none;
  border-radius: 5px;
  background: none;
  cursor: pointer;
  text-align: left;
  transition: background 0.1s;
  color: var(--color-text);
}

.mention-menu__item:hover,
.mention-menu__item--active {
  background: var(--color-active);
}

.mention-menu__icon {
  flex-shrink: 0;
  color: var(--color-text-muted);
  display: flex;
  align-items: center;
}

.mention-menu__title {
  flex: 1;
  font-size: 0.875rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.mention-menu__type {
  font-size: 0.7rem;
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.mention-menu__empty {
  padding: 8px 10px;
  font-size: 0.8125rem;
  color: var(--color-text-muted);
}
</style>

<script setup lang="ts">
import { ref, computed, nextTick, onMounted, onUnmounted } from 'vue'
import { Icon } from '@iconify/vue'
import { useEscapeKey } from '@/composables/useEscapeStack'
import mdiData from '@iconify-json/mdi/icons.json'
import tablerData from '@iconify-json/tabler/icons.json'
import lucideData from '@iconify-json/lucide/icons.json'

// ── Icon index ────────────────────────────────────────────────────────────────

const ALL_ICONS: string[] = [
  ...Object.keys(mdiData.icons).map((n) => `mdi:${n}`),
  ...Object.keys(tablerData.icons).map((n) => `tabler:${n}`),
  ...Object.keys(lucideData.icons).map((n) => `lucide:${n}`),
]

// ── Props / Emits ─────────────────────────────────────────────────────────────

const props = defineProps<{
  modelValue?: string | null
  /**
   * When provided the picker is rendered via <Teleport to="body"> with
   * position: fixed, anchored just below the trigger element.
   * When absent the picker uses position: absolute (inline, existing callers).
   */
  triggerRect?: DOMRect | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string | null]
  'close': []
}>()

// ── State ─────────────────────────────────────────────────────────────────────

const searchRef = ref<HTMLInputElement | null>(null)
const query = ref('')
const containerRef = ref<HTMLElement | null>(null)

// ── Common icons shown when the search field is empty ─────────────────────────

const COMMON: string[] = [
  'mdi:file-document-outline', 'mdi:folder-outline',     'mdi:star-outline',       'mdi:heart-outline',
  'mdi:bookmark-outline',      'mdi:flag-outline',        'mdi:tag-outline',        'mdi:pencil-outline',
  'mdi:lightbulb-outline',     'mdi:calendar-outline',    'mdi:clock-outline',      'mdi:check-circle-outline',
  'mdi:alert-circle-outline',  'mdi:information-outline', 'mdi:archive-outline',    'mdi:trash-can-outline',
  'mdi:home-outline',          'mdi:account-outline',     'mdi:cog-outline',        'mdi:table-large',
  'mdi:chart-bar',             'mdi:image-outline',       'mdi:link-variant',       'mdi:code-tags',
]

// ── Search ────────────────────────────────────────────────────────────────────

const results = computed<string[]>(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return COMMON
  return ALL_ICONS
    .filter((icon) => icon.slice(icon.indexOf(':') + 1).includes(q))
    .slice(0, 120)
})

// ── Teleport positioning ──────────────────────────────────────────────────────

const PICKER_WIDTH  = 280
const PICKER_HEIGHT = 270 // approximate; search bar + ~3 rows

/**
 * When triggerRect is provided, compute a fixed position that keeps the
 * picker inside the viewport. Anchors below the trigger; flips upward if
 * there is not enough room below.
 */
const fixedStyle = computed<Record<string, string> | null>(() => {
  if (!props.triggerRect) return null

  const r   = props.triggerRect
  const vw  = window.innerWidth
  const vh  = window.innerHeight
  const GAP = 4

  let top  = r.bottom + GAP
  let left = r.left

  // Flip upward if not enough space below.
  if (top + PICKER_HEIGHT > vh) {
    top = r.top - PICKER_HEIGHT - GAP
  }

  // Clamp horizontally.
  if (left + PICKER_WIDTH > vw) {
    left = vw - PICKER_WIDTH - 8
  }
  if (left < 8) left = 8

  return {
    position: 'fixed',
    top:  `${Math.round(top)}px`,
    left: `${Math.round(left)}px`,
    zIndex: '9999',
  }
})

const useTeleport = computed(() => !!props.triggerRect)

// ── Lifecycle ─────────────────────────────────────────────────────────────────

onMounted(async () => {
  await nextTick()
  searchRef.value?.focus()
  document.addEventListener('mousedown', onOutsideClick)
})

onUnmounted(() => {
  document.removeEventListener('mousedown', onOutsideClick)
})

function onOutsideClick(e: MouseEvent): void {
  if (containerRef.value && !containerRef.value.contains(e.target as Node)) {
    emit('close')
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function iconTitle(icon: string): string {
  const colon  = icon.indexOf(':')
  const prefix = icon.slice(0, colon)
  const name   = icon.slice(colon + 1)
  return `${name} (${prefix})`
}

function select(icon: string): void {
  emit('update:modelValue', icon)
  emit('close')
}

function clear(): void {
  emit('update:modelValue', null)
  emit('close')
}

// Close on Escape via the shared overlay stack. The picker is only mounted
// while open, so it registers for its whole lifetime; when it floats above a
// SideView it therefore intercepts Escape before the panel does, and it no
// longer depends on the search field holding focus.
useEscapeKey(() => emit('close'))
</script>

<template>
  <!-- Teleport mode: rendered at <body> level with fixed positioning. -->
  <Teleport v-if="useTeleport" to="body">
    <div
      ref="containerRef"
      class="icon-picker"
      :style="fixedStyle ?? undefined"
    >
      <div class="icon-picker__header">
        <Icon icon="mdi:magnify" width="15" height="15" class="icon-picker__search-icon" />
        <input
          ref="searchRef"
          v-model="query"
          class="icon-picker__search"
          placeholder="Symbol suchen…"
          spellcheck="false"
        />
        <button v-if="modelValue" class="icon-picker__clear" title="Symbol entfernen" @click="clear">
          <Icon icon="mdi:close" width="14" height="14" />
        </button>
      </div>
      <div class="icon-picker__grid">
        <button
          v-for="icon in results"
          :key="icon"
          class="icon-picker__cell"
          :class="{ 'icon-picker__cell--active': icon === modelValue }"
          :title="iconTitle(icon)"
          @click="select(icon)"
        >
          <Icon :icon="icon" width="20" height="20" />
        </button>
        <div v-if="results.length === 0" class="icon-picker__empty">
          Keine Ergebnisse.
        </div>
      </div>
    </div>
  </Teleport>

  <!-- Inline mode: position: absolute, existing callers unchanged. -->
  <div
    v-else
    ref="containerRef"
    class="icon-picker"
  >
    <div class="icon-picker__header">
      <Icon icon="mdi:magnify" width="15" height="15" class="icon-picker__search-icon" />
      <input
        ref="searchRef"
        v-model="query"
        class="icon-picker__search"
        placeholder="Symbol suchen…"
        spellcheck="false"
      />
      <button v-if="modelValue" class="icon-picker__clear" title="Symbol entfernen" @click="clear">
        <Icon icon="mdi:close" width="14" height="14" />
      </button>
    </div>
    <div class="icon-picker__grid">
      <button
        v-for="icon in results"
        :key="icon"
        class="icon-picker__cell"
        :class="{ 'icon-picker__cell--active': icon === modelValue }"
        :title="iconTitle(icon)"
        @click="select(icon)"
      >
        <Icon :icon="icon" width="20" height="20" />
      </button>
      <div v-if="results.length === 0" class="icon-picker__empty">
        Keine Ergebnisse.
      </div>
    </div>
  </div>
</template>

<style scoped>
.icon-picker {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
  width: 280px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  /* Only needed for inline (absolute) mode; ignored when fixed is set inline. */
  position: absolute;
  z-index: 100;
}

.icon-picker__header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 10px;
  border-bottom: 1px solid var(--color-border);
}

.icon-picker__search-icon {
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.icon-picker__search {
  flex: 1;
  background: none;
  border: none;
  outline: none;
  font-size: 0.8125rem;
  color: var(--color-text);
  font-family: inherit;
}

.icon-picker__clear {
  display: flex;
  align-items: center;
  background: none;
  border: none;
  padding: 2px;
  border-radius: 3px;
  cursor: pointer;
  color: var(--color-text-muted);
  transition: color 0.1s, background 0.1s;
}

.icon-picker__clear:hover {
  background: var(--color-border);
  color: var(--color-text);
}

.icon-picker__grid {
  display: grid;
  grid-template-columns: repeat(8, 1fr);
  gap: 2px;
  padding: 6px;
  max-height: 220px;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: var(--color-border) transparent;
}

.icon-picker__cell {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: none;
  border-radius: 4px;
  background: none;
  cursor: pointer;
  color: var(--color-text-muted);
  transition: background 0.1s, color 0.1s;
}

.icon-picker__cell:hover {
  background: var(--color-hover);
  color: var(--color-text);
}

.icon-picker__cell--active {
  background: var(--color-accent-subtle);
  color: var(--color-accent);
}

.icon-picker__empty {
  grid-column: 1 / -1;
  text-align: center;
  padding: 1rem;
  font-size: 0.8rem;
  color: var(--color-text-muted);
}
</style>

<script setup lang="ts">
import { ref, onUnmounted } from 'vue'
import NavView from '@/components/nav/NavView.vue'
import MainView from '@/components/main/MainView.vue'
import SettingsModal from '@/components/settings/SettingsModal.vue'
import HelpModal from '@/components/settings/HelpModal.vue'
import { useUiStore } from '@/stores/ui'
import { useSettingsModal } from '@/composables/useSettingsModal'
import { useHelpModal } from '@/composables/useHelpModal'

defineProps<{
  blockId?: string | null
}>()

const ui = useUiStore()
const { isOpen: settingsOpen } = useSettingsModal()
const { isOpen: helpOpen } = useHelpModal()
const isResizing = ref(false)

function onResizeStart(e: MouseEvent): void {
  e.preventDefault()
  isResizing.value = true
  document.addEventListener('mousemove', onResizeMove)
  document.addEventListener('mouseup', onResizeEnd)
}

function onResizeMove(e: MouseEvent): void {
  if (!isResizing.value) return
  ui.clampSidebarWidth(e.clientX)
}

function onResizeEnd(): void {
  isResizing.value = false
  document.removeEventListener('mousemove', onResizeMove)
  document.removeEventListener('mouseup', onResizeEnd)
}

onUnmounted(() => {
  document.removeEventListener('mousemove', onResizeMove)
  document.removeEventListener('mouseup', onResizeEnd)
})
</script>

<template>
  <div class="app-shell" :class="{ 'is-resizing': isResizing }">
    <aside class="sidebar" :style="{ width: ui.sidebarWidth + 'px' }">
      <NavView />
    </aside>

    <div
      class="resize-handle"
      @mousedown="onResizeStart"
      aria-hidden="true"
    />

    <main class="main-area">
      <MainView :block-id="blockId ?? null" />
    </main>

    <SettingsModal v-if="settingsOpen" />
    <HelpModal v-if="helpOpen" />
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  flex-direction: row;
  height: 100vh;
  height: 100svh;
  overflow: hidden;
  background: var(--color-bg);
}

.app-shell.is-resizing {
  cursor: col-resize;
  user-select: none;
}

.sidebar {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--color-sidebar-bg);
  border-right: 1px solid var(--color-border);
  overflow: hidden;
}

.resize-handle {
  width: 4px;
  flex-shrink: 0;
  cursor: col-resize;
  background: transparent;
  transition: background 0.15s;
  position: relative;
  z-index: 10;
}

.resize-handle:hover,
.app-shell.is-resizing .resize-handle {
  background: var(--color-accent);
}

.main-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

/* ── Print ───────────────────────────────────────────────────────────────── */
@media print {
  .app-shell {
    height: auto !important;
    overflow: visible !important;
    display: block !important;
  }

  .sidebar,
  .resize-handle {
    display: none !important;
  }

  .main-area {
    height: auto !important;
    overflow: visible !important;
    display: block !important;
  }
}
</style>

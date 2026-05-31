import { ref, watch } from 'vue'
import { defineStore } from 'pinia'

const SIDEBAR_MIN = 180
const SIDEBAR_MAX = 480
const SIDEBAR_DEFAULT = 260
const STORAGE_KEY = 'capybarca-sidebar-width'

export const useUiStore = defineStore('ui', () => {
  const sidebarWidth = ref(
    parseInt(localStorage.getItem(STORAGE_KEY) ?? String(SIDEBAR_DEFAULT), 10),
  )

  watch(sidebarWidth, (val) => {
    localStorage.setItem(STORAGE_KEY, String(val))
  })

  function clampSidebarWidth(px: number): void {
    sidebarWidth.value = Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, px))
  }

  return {
    sidebarWidth,
    clampSidebarWidth,
    SIDEBAR_MIN,
    SIDEBAR_MAX,
  }
})

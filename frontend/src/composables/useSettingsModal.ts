/**
 * useSettingsModal
 *
 * Singleton composable that controls the global settings modal.
 * Any component can call ``openSettings()`` / ``closeSettings()`` and
 * the modal mounted in AppView reacts reactively.
 */
import { ref } from 'vue'

const isOpen = ref(false)

export function useSettingsModal() {
  function openSettings(): void {
    isOpen.value = true
  }

  function closeSettings(): void {
    isOpen.value = false
  }

  return { isOpen, openSettings, closeSettings }
}

/**
 * useHelpModal
 *
 * Singleton composable that controls the global help modal.
 * Any component can call ``openHelp()`` / ``closeHelp()`` and
 * the modal mounted in AppView reacts reactively.
 */
import { ref } from 'vue'

const isOpen = ref(false)

export function useHelpModal() {
  function openHelp(): void {
    isOpen.value = true
  }

  function closeHelp(): void {
    isOpen.value = false
  }

  return { isOpen, openHelp, closeHelp }
}

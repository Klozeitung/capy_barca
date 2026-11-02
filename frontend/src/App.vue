<script setup lang="ts">
import '@/assets/main.css'
import { watch } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useWsStore } from '@/stores/ws'
import { useAlertsStore } from '@/stores/alerts'

const auth = useAuthStore()
const ws = useWsStore()
const alerts = useAlertsStore()
const router = useRouter()

// Connect the WebSocket whenever the user is authenticated and disconnect
// when they log out. App.vue never unmounts, so a lifecycle-based composable
// would not handle the login/logout cycle correctly – a watcher is the right
// tool here. immediate: true ensures the connection is established on first
// render if the session is already valid (e.g. after a page reload).
// Alerts follow the same cycle: pending ones are shown after login, and
// opening an alert notice goes to its entry.
watch(
  () => auth.isAuthenticated,
  (authenticated) => {
    if (authenticated) {
      ws.connect()
      void alerts.start((blockId) => { void router.push(`/blocks/${blockId}`) })
    } else {
      ws.disconnect()
      alerts.stop()
    }
  },
  { immediate: true },
)
</script>

<template>
  <router-view />
</template>

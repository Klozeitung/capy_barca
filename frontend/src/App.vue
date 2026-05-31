<script setup lang="ts">
import '@/assets/main.css'
import { watch } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { useWsStore } from '@/stores/ws'

const auth = useAuthStore()
const ws = useWsStore()

// Connect the WebSocket whenever the user is authenticated and disconnect
// when they log out. App.vue never unmounts, so a lifecycle-based composable
// would not handle the login/logout cycle correctly – a watcher is the right
// tool here. immediate: true ensures the connection is established on first
// render if the session is already valid (e.g. after a page reload).
watch(
  () => auth.isAuthenticated,
  (authenticated) => {
    if (authenticated) {
      ws.connect()
    } else {
      ws.disconnect()
    }
  },
  { immediate: true },
)
</script>

<template>
  <router-view />
</template>

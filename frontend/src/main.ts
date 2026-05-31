import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import router from './router'
import i18n from './plugins/i18n'
import { useAuthStore } from '@/stores/auth'

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)
app.use(i18n)

// Verify session BEFORE installing the router so that the initial navigation
// guard already has the correct isAuthenticated state. If the router is
// installed first, it triggers navigation immediately (during install), and
// the guard sees isAuthenticated=false regardless of a valid session cookie.
const auth = useAuthStore()
await auth.verify()

app.use(router)
app.mount('#app')

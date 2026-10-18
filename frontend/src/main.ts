import { createApp } from 'vue'
import { createPinia } from 'pinia'

import '@vuepic/vue-datepicker/dist/main.css'

import App from './App.vue'
import router from './router'
import i18n from './plugins/i18n'
import { registerIconSets } from './plugins/icons'
import { useAuthStore } from '@/stores/auth'

// Icons render from bundled data; see plugins/icons.ts.
registerIconSets()

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

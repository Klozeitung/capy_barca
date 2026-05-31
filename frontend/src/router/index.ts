import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import AuthView from '@/views/AuthView.vue'
import AppView from '@/views/AppView.vue'

declare module 'vue-router' {
  interface RouteMeta {
    requiresAuth?: boolean
  }
}

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/login',
      component: AuthView,
    },
    {
      // No blockId: shows AppView with empty main area.
      path: '/',
      component: AppView,
      meta: { requiresAuth: true },
      props: { blockId: null },
    },
    {
      path: '/blocks/:blockId',
      component: AppView,
      meta: { requiresAuth: true },
      props: true,
    },
  ],
})

router.beforeEach((to) => {
  const auth = useAuthStore()
  if (to.meta.requiresAuth && !auth.isAuthenticated) {
    return '/login'
  }
})

export default router

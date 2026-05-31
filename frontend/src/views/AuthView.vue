<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { apiClient, ApiError } from '@/api/client'
import { useI18n } from 'vue-i18n'

type AuthMode = 'login' | 'register' | 'signup' | null

const { t } = useI18n()
const router = useRouter()
const auth = useAuthStore()

const mode = ref<AuthMode>(null)
const allowNewUsers = ref(false)

const username = ref('')
const password = ref('')
const passwordConfirm = ref('')
const error = ref('')
const loading = ref(false)

onMounted(async () => {
  try {
    const data = await apiClient.get<{ configured: boolean; allow_new_users: boolean }>(
      '/api/setup-status',
    )
    allowNewUsers.value = data.allow_new_users
    mode.value = data.configured ? 'login' : 'register'
  } catch {
    error.value = 'Verbindung zum Server fehlgeschlagen.'
  }
})

function switchToSignup(): void {
  if (!allowNewUsers.value) return
  error.value = ''
  username.value = ''
  password.value = ''
  passwordConfirm.value = ''
  mode.value = 'signup'
}

function switchToLogin(): void {
  error.value = ''
  username.value = ''
  password.value = ''
  passwordConfirm.value = ''
  mode.value = 'login'
}

async function handleSubmit(): Promise<void> {
  error.value = ''

  if ((mode.value === 'register' || mode.value === 'signup') && password.value !== passwordConfirm.value) {
    error.value = 'Passwörter stimmen nicht überein.'
    return
  }

  if (mode.value === 'signup' && password.value.length < 8) {
    error.value = 'Das Passwort muss mindestens 8 Zeichen lang sein.'
    return
  }

  loading.value = true

  const endpoint =
    mode.value === 'login'
      ? '/api/login'
      : mode.value === 'register'
        ? '/api/register'
        : '/api/signup'

  try {
    const data = await apiClient.post<{ success: boolean; username: string; role: string }>(
      endpoint,
      { username: username.value, password: password.value },
    )
    auth.login(data.username, data.role)
    router.push('/')
  } catch (e) {
    if (e instanceof ApiError) {
      if (e.status === 401) {
        error.value = 'Ungültige Anmeldedaten.'
      } else if (e.status === 409) {
        error.value = 'Dieser Benutzername ist bereits vergeben.'
      } else if (e.status === 403) {
        error.value = t('auth.signupDisabled')
      } else if (e.status === 422) {
        error.value = 'Das Passwort muss mindestens 8 Zeichen lang sein.'
      } else {
        error.value =
          mode.value === 'login' ? 'Ungültige Anmeldedaten.' : 'Registrierung fehlgeschlagen.'
      }
    } else {
      error.value = 'Verbindung zum Server fehlgeschlagen.'
    }
  } finally {
    loading.value = false
  }
}

function headingText(): string {
  if (mode.value === 'login') return 'Anmelden'
  if (mode.value === 'register') return 'Einrichten'
  return t('auth.signupTitle')
}

function submitLabel(): string {
  if (loading.value) return '...'
  if (mode.value === 'login') return 'Anmelden'
  if (mode.value === 'signup') return t('auth.signupTitle')
  return 'Zugang anlegen'
}
</script>

<template>
  <div class="auth-wrapper">
    <div v-if="mode === null && !error" class="auth-box">
      <img src="/CapyBarca.png" alt="CapyBarca" class="logo" />
    </div>

    <div v-else-if="mode === null && error" class="auth-box">
      <img src="/CapyBarca.png" alt="CapyBarca" class="logo" />
      <p class="error">{{ error }}</p>
    </div>

    <div v-else class="auth-box">
      <img src="/CapyBarca.png" alt="CapyBarca" class="logo" />
      <h1>{{ headingText() }}</h1>

      <p v-if="mode === 'register'" class="hint">Willkommen. Lege jetzt deinen Zugang an.</p>
      <p v-if="mode === 'signup'" class="hint">{{ t('auth.signupHint') }}</p>
      <p v-if="error" class="error">{{ error }}</p>

      <form @submit.prevent="handleSubmit">
        <div class="field">
          <label for="username">Benutzername</label>
          <input id="username" v-model="username" type="text" autocomplete="username" required />
        </div>
        <div class="field">
          <label for="password">Passwort</label>
          <input
            id="password"
            v-model="password"
            type="password"
            :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
            required
          />
        </div>
        <div v-if="mode === 'register' || mode === 'signup'" class="field">
          <label for="password-confirm">Passwort bestätigen</label>
          <input
            id="password-confirm"
            v-model="passwordConfirm"
            type="password"
            autocomplete="new-password"
            required
          />
        </div>
        <button type="submit" :disabled="loading">{{ submitLabel() }}</button>
      </form>

      <div v-if="mode === 'login'" class="auth-switch">
        <button
          class="auth-switch__btn"
          :class="{ 'auth-switch__btn--disabled': !allowNewUsers }"
          :disabled="!allowNewUsers"
          :title="!allowNewUsers ? t('auth.signupDisabled') : undefined"
          @click="switchToSignup"
        >
          {{ t('auth.registerLink') }}
        </button>
      </div>

      <div v-if="mode === 'signup'" class="auth-switch">
        <button class="auth-switch__btn" @click="switchToLogin">
          {{ t('auth.loginLink') }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.auth-wrapper {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
}

.auth-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1.25rem;
  width: 340px;
}

.logo { width: 120px; height: auto; }

h1 { margin: 0; font-size: 1.5rem; align-self: flex-start; }

.hint { margin: 0; font-size: 0.875rem; opacity: 0.7; align-self: flex-start; }

.error { color: #e05252; font-size: 0.875rem; margin: 0; align-self: flex-start; }

form { display: flex; flex-direction: column; gap: 1rem; width: 100%; }

.field { display: flex; flex-direction: column; gap: 0.25rem; }

label { font-size: 0.875rem; }

input {
  padding: 0.5rem 0.625rem;
  font-size: 1rem;
  border: 1px solid #444;
  border-radius: 4px;
  background: transparent;
  color: inherit;
}

button[type="submit"] {
  padding: 0.6rem;
  font-size: 1rem;
  cursor: pointer;
  border: none;
  border-radius: 4px;
  background: #4a7c59;
  color: #fff;
  margin-top: 0.25rem;
}

button[type="submit"]:disabled { opacity: 0.55; cursor: not-allowed; }

.auth-switch {
  width: 100%;
  display: flex;
  justify-content: center;
}

.auth-switch__btn {
  background: none;
  border: none;
  padding: 0;
  font-size: 0.8125rem;
  color: #4a7c59;
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 2px;
  transition: opacity 0.12s;
}

.auth-switch__btn:hover:not(.auth-switch__btn--disabled) { opacity: 0.75; }

.auth-switch__btn--disabled {
  color: var(--color-text-muted, #888);
  cursor: not-allowed;
  text-decoration: none;
  opacity: 0.5;
}
</style>

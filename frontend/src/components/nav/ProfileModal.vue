<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { Icon } from '@iconify/vue'
import { useRouter } from 'vue-router'
import { apiClient, ApiError } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const emit = defineEmits<{ (e: 'close'): void }>()

const router = useRouter()
const auth = useAuthStore()

// ── State ─────────────────────────────────────────────────────────────────────

const newUsername = ref('')
const usernameError = ref('')
const usernameSuccess = ref(false)
const usernameSaving = ref(false)

const currentPassword = ref('')
const newPassword = ref('')
const newPasswordConfirm = ref('')
const passwordError = ref('')
const passwordSuccess = ref(false)
const passwordSaving = ref(false)

// ── Init ──────────────────────────────────────────────────────────────────────

onMounted(() => {
  newUsername.value = auth.username
})

// ── Username ──────────────────────────────────────────────────────────────────

async function saveUsername(): Promise<void> {
  usernameError.value = ''
  usernameSuccess.value = false
  if (!newUsername.value.trim()) {
    usernameError.value = 'Benutzername darf nicht leer sein.'
    return
  }
  usernameSaving.value = true
  try {
    await apiClient.patch('/api/users/me', { username: newUsername.value.trim() })
    await auth.verify()
    usernameSuccess.value = true
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) {
      usernameError.value = 'Dieser Benutzername ist bereits vergeben.'
    } else {
      usernameError.value = 'Speichern fehlgeschlagen.'
    }
  } finally {
    usernameSaving.value = false
  }
}

// ── Password ──────────────────────────────────────────────────────────────────

async function savePassword(): Promise<void> {
  passwordError.value = ''
  passwordSuccess.value = false
  if (!currentPassword.value || !newPassword.value) {
    passwordError.value = 'Alle Felder sind erforderlich.'
    return
  }
  if (newPassword.value !== newPasswordConfirm.value) {
    passwordError.value = 'Neue Passwörter stimmen nicht überein.'
    return
  }
  if (newPassword.value.length < 8) {
    passwordError.value = 'Das neue Passwort muss mindestens 8 Zeichen lang sein.'
    return
  }
  passwordSaving.value = true
  try {
    await apiClient.patch('/api/users/me/password', {
      current_password: currentPassword.value,
      new_password: newPassword.value,
    })
    passwordSuccess.value = true
    currentPassword.value = ''
    newPassword.value = ''
    newPasswordConfirm.value = ''
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) {
      passwordError.value = 'Aktuelles Passwort ist falsch.'
    } else {
      passwordError.value = 'Speichern fehlgeschlagen.'
    }
  } finally {
    passwordSaving.value = false
  }
}

// ── Logout ────────────────────────────────────────────────────────────────────

async function logout(): Promise<void> {
  await auth.logout()
  emit('close')
  router.push('/login')
}

// ── Backdrop ──────────────────────────────────────────────────────────────────

function onBackdrop(e: MouseEvent): void {
  if ((e.target as HTMLElement).classList.contains('pm-backdrop')) {
    emit('close')
  }
}

// ── Avatar ────────────────────────────────────────────────────────────────────

function avatarLetter(name: string): string {
  return (name || '?').charAt(0).toUpperCase()
}
</script>

<template>
  <Teleport to="body">
    <div class="pm-backdrop" @click="onBackdrop">
      <div class="pm" role="dialog" aria-modal="true" aria-label="Profil">

        <!-- Header -->
        <div class="pm__header">
          <span class="pm__title">
            <Icon icon="mdi:account-outline" width="15" height="15" />
            Profil
          </span>
          <button class="pm__close" aria-label="Schliessen" @click="emit('close')">
            <Icon icon="mdi:close" width="15" height="15" />
          </button>
        </div>

        <!-- User info strip -->
        <div class="pm__identity">
          <div class="pm__avatar">{{ avatarLetter(auth.username) }}</div>
          <div class="pm__identity-text">
            <span class="pm__identity-name">{{ auth.username }}</span>
            <span class="pm__role-badge" :class="`pm__role-badge--${auth.role}`">
              {{ auth.role === 'admin' ? 'Admin' : 'Mitglied' }}
            </span>
          </div>
        </div>

        <div class="pm__body">

          <!-- Username section -->
          <section class="pm__section">
            <h3 class="pm__section-title">Benutzername</h3>
            <div class="pm__row">
              <input
                v-model="newUsername"
                class="pm__input"
                type="text"
                autocomplete="username"
                placeholder="Benutzername"
                @keydown.enter="saveUsername"
              />
              <button
                class="pm__btn pm__btn--primary"
                :disabled="usernameSaving || newUsername.trim() === auth.username"
                @click="saveUsername"
              >
                {{ usernameSaving ? '...' : 'Speichern' }}
              </button>
            </div>
            <p v-if="usernameError" class="pm__feedback pm__feedback--error">{{ usernameError }}</p>
            <p v-if="usernameSuccess" class="pm__feedback pm__feedback--ok">Benutzername gespeichert.</p>
          </section>

          <div class="pm__divider" />

          <!-- Password section -->
          <section class="pm__section">
            <h3 class="pm__section-title">Passwort ändern</h3>
            <div class="pm__field">
              <label class="pm__label">Aktuelles Passwort</label>
              <input
                v-model="currentPassword"
                class="pm__input"
                type="password"
                autocomplete="current-password"
              />
            </div>
            <div class="pm__field">
              <label class="pm__label">Neues Passwort</label>
              <input
                v-model="newPassword"
                class="pm__input"
                type="password"
                autocomplete="new-password"
              />
            </div>
            <div class="pm__field">
              <label class="pm__label">Neues Passwort bestätigen</label>
              <input
                v-model="newPasswordConfirm"
                class="pm__input"
                type="password"
                autocomplete="new-password"
                @keydown.enter="savePassword"
              />
            </div>
            <p v-if="passwordError" class="pm__feedback pm__feedback--error">{{ passwordError }}</p>
            <p v-if="passwordSuccess" class="pm__feedback pm__feedback--ok">Passwort erfolgreich geändert.</p>
            <div class="pm__actions-right">
              <button
                class="pm__btn pm__btn--primary"
                :disabled="passwordSaving"
                @click="savePassword"
              >
                {{ passwordSaving ? '...' : 'Speichern' }}
              </button>
            </div>
          </section>

        </div>

        <!-- Footer with logout -->
        <div class="pm__footer">
          <button class="pm__btn pm__btn--logout" @click="logout">
            <Icon icon="mdi:logout" width="14" height="14" />
            Abmelden
          </button>
        </div>

      </div>
    </div>
  </Teleport>
</template>

<style scoped>
/* ── Backdrop ────────────────────────────────────────────────────────────── */
.pm-backdrop {
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: rgba(0, 0, 0, 0.35);
  display: flex;
  align-items: center;
  justify-content: center;
}

/* ── Dialog ──────────────────────────────────────────────────────────────── */
.pm {
  background: var(--color-bg-surface, var(--color-bg));
  border: 1px solid var(--color-border);
  border-radius: 10px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
  width: 400px;
  max-height: 90vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* ── Header ──────────────────────────────────────────────────────────────── */
.pm__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px 10px;
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.pm__title {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--color-text);
}

.pm__close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border: none;
  border-radius: 5px;
  background: none;
  cursor: pointer;
  color: var(--color-text-muted);
  transition: background 0.12s, color 0.12s;
}

.pm__close:hover {
  background: var(--color-hover);
  color: var(--color-text);
}

/* ── Identity strip ──────────────────────────────────────────────────────── */
.pm__identity {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.pm__avatar {
  width: 38px;
  height: 38px;
  border-radius: 50%;
  background: var(--color-accent);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1rem;
  font-weight: 700;
  flex-shrink: 0;
}

.pm__identity-text {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.pm__identity-name {
  font-size: 0.9375rem;
  font-weight: 600;
  color: var(--color-text);
}

.pm__role-badge {
  font-size: 0.7rem;
  font-weight: 600;
  padding: 2px 7px;
  border-radius: 20px;
  letter-spacing: 0.03em;
}

.pm__role-badge--admin {
  background: color-mix(in srgb, var(--color-accent) 15%, transparent);
  color: var(--color-accent);
}

.pm__role-badge--member {
  background: var(--color-hover);
  color: var(--color-text-muted);
}

/* ── Body ────────────────────────────────────────────────────────────────── */
.pm__body {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 0;
}

.pm__divider {
  height: 1px;
  background: var(--color-border);
  margin: 14px 0;
}

/* ── Section ─────────────────────────────────────────────────────────────── */
.pm__section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.pm__section-title {
  font-size: 0.72rem;
  font-weight: 600;
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin: 0 0 2px;
}

/* ── Form elements ───────────────────────────────────────────────────────── */
.pm__row {
  display: flex;
  gap: 8px;
}

.pm__field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.pm__label {
  font-size: 0.8125rem;
  color: var(--color-text-muted);
}

.pm__input {
  flex: 1;
  padding: 6px 10px;
  font-size: 0.875rem;
  border: 1px solid var(--color-border);
  border-radius: 5px;
  background: transparent;
  color: var(--color-text);
  font-family: inherit;
  transition: border-color 0.12s;
}

.pm__input:focus {
  outline: none;
  border-color: var(--color-accent);
}

/* ── Feedback ────────────────────────────────────────────────────────────── */
.pm__feedback {
  font-size: 0.8125rem;
  margin: 0;
}

.pm__feedback--error { color: #e05252; }
.pm__feedback--ok    { color: #4a9e6a; }

/* ── Actions ─────────────────────────────────────────────────────────────── */
.pm__actions-right {
  display: flex;
  justify-content: flex-end;
  margin-top: 4px;
}

/* ── Buttons ─────────────────────────────────────────────────────────────── */
.pm__btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  border: none;
  border-radius: 5px;
  font-size: 0.8125rem;
  font-family: inherit;
  cursor: pointer;
  transition: opacity 0.12s, background 0.12s;
}

.pm__btn--primary {
  background: var(--color-accent);
  color: #fff;
  font-weight: 600;
}

.pm__btn--primary:hover:not(:disabled) {
  opacity: 0.85;
}

.pm__btn--primary:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

/* ── Footer ──────────────────────────────────────────────────────────────── */
.pm__footer {
  flex-shrink: 0;
  border-top: 1px solid var(--color-border);
  padding: 10px 16px;
}

.pm__btn--logout {
  background: none;
  color: #e05252;
  padding: 6px 10px;
  border: 1px solid color-mix(in srgb, #e05252 35%, transparent);
  font-weight: 500;
}

.pm__btn--logout:hover {
  background: color-mix(in srgb, #e05252 10%, transparent);
}
</style>

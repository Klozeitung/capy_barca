<script setup lang="ts">
/**
 * SettingsModal
 *
 * Two-pane settings dialog:
 *   Left  – section navigation
 *   Right – content for the active section
 *
 * Sections
 * --------
 *  language – Language   (locale switcher, available to all users)
 *  storage  – Storage    (capacity cards)
 *  backup   – Backup     (admin-only: backup script download + instructions)
 *  users    – Users      (admin-only: user management)
 */
import { ref, computed, watch, onMounted } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { useSettingsModal } from '@/composables/useSettingsModal'
import { useAuthStore } from '@/stores/auth'
import { apiClient, ApiError } from '@/api/client'
import { setLocale } from '@/plugins/i18n'

const { t } = useI18n()
const { closeSettings } = useSettingsModal()
const auth = useAuthStore()

// ── Types ─────────────────────────────────────────────────────────────────────

interface CapacityData {
  total_bytes: number
  used_bytes: number
  free_bytes: number
}

interface UserRow {
  id: string
  username: string
  role: string
  is_active: boolean
}

// ── State: general ─────────────────────────────────────────────────────────────

const activeSection = ref<string>('language')

// ── State: language ────────────────────────────────────────────────────────────

type SupportedLocale = 'de' | 'en'

const { locale } = useI18n()

const currentLocale = ref<SupportedLocale>(locale.value as SupportedLocale)

const LANGUAGES: { code: SupportedLocale; labelKey: string; flag: string }[] = [
  { code: 'de', labelKey: 'settings.languageDe', flag: '🇩🇪' },
  { code: 'en', labelKey: 'settings.languageEn', flag: '🇬🇧' },
]

function selectLocale(code: SupportedLocale): void {
  currentLocale.value = code
  setLocale(code)
}

// ── State: date format ─────────────────────────────────────────────────────────

const DATE_FORMAT_OPTIONS = ['DD.MM.YYYY', 'MM.DD.YYYY', 'YYYY-MM-DD', 'YYYY-DD-MM'] as const
type DateFormatToken = typeof DATE_FORMAT_OPTIONS[number]

const dateFormatPref = ref<string>(auth.dateFormat)
const dateFormatSaving = ref<DateFormatToken | null>(null)
const dateFormatError = ref(false)

/** Render a fixed sample date (31 Dec 2026) in the given token for preview. */
function dateFormatSample(fmt: DateFormatToken): string {
  const y = '2026', m = '12', d = '31'
  switch (fmt) {
    case 'MM.DD.YYYY': return `${m}.${d}.${y}`
    case 'YYYY-MM-DD': return `${y}-${m}-${d}`
    case 'YYYY-DD-MM': return `${y}-${d}-${m}`
    case 'DD.MM.YYYY':
    default:           return `${d}.${m}.${y}`
  }
}

async function selectDateFormat(fmt: DateFormatToken): Promise<void> {
  if (fmt === dateFormatPref.value) return
  dateFormatSaving.value = fmt
  dateFormatError.value = false
  try {
    await apiClient.patch('/api/users/me/date-format', { date_format: fmt })
    dateFormatPref.value = fmt
    auth.setDateFormat(fmt)
  } catch {
    dateFormatError.value = true
  } finally {
    dateFormatSaving.value = null
  }
}

// ── State: storage ─────────────────────────────────────────────────────────────

const capacity = ref<CapacityData | null>(null)
const capacityError = ref(false)

// ── State: backup ─────────────────────────────────────────────────────────────

const backupDownloading = ref(false)
const backupError = ref(false)

// ── State: users ──────────────────────────────────────────────────────────────

const usersData = ref<UserRow[]>([])
const usersLoading = ref(false)
const usersError = ref(false)

// Per-row UI state
const openResetPw = ref<string | null>(null)   // user_id with pw reset form open
const resetPwValue = ref('')
const resetPwSaving = ref(false)
const resetPwFeedback = ref<{ id: string; msg: string; ok: boolean } | null>(null)

const confirmDeactivate = ref<string | null>(null)   // user_id pending confirm
const roleChanging = ref<string | null>(null)        // user_id having role changed

// New user creation
const showNewUser = ref(false)
const newUsername = ref('')
const newPassword = ref('')
const newRole = ref<'member' | 'admin'>('member')
const newUserSaving = ref(false)
const newUserFeedback = ref<{ msg: string; ok: boolean } | null>(null)

// ── Computed ──────────────────────────────────────────────────────────────────

/** Users excluding the currently logged-in admin (managed via ProfileModal). */
const otherUsers = computed(() =>
  usersData.value.filter((u) => u.username !== auth.username),
)

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatBytes(bytes: number): string {
  if (bytes >= 1_073_741_824) return `${(bytes / 1_073_741_824).toFixed(1)} GB`
  if (bytes >= 1_048_576)     return `${(bytes / 1_048_576).toFixed(1)} MB`
  if (bytes >= 1_024)         return `${(bytes / 1_024).toFixed(1)} KB`
  return `${bytes} B`
}

function usedPercent(data: CapacityData): number {
  if (data.total_bytes === 0) return 0
  return Math.round((data.used_bytes / data.total_bytes) * 100)
}

function roleBadgeClass(role: string): string {
  return role === 'admin' ? 'badge--admin' : 'badge--member'
}

function roleLabel(role: string): string {
  return role === 'admin' ? t('settings.usersRoleAdmin') : t('settings.usersRoleMember')
}

// ── Data fetching ─────────────────────────────────────────────────────────────

async function fetchCapacity(): Promise<void> {
  capacityError.value = false
  try {
    const res = await fetch('/api/media/capacity', { credentials: 'include' })
    if (!res.ok) throw new Error()
    capacity.value = (await res.json()) as CapacityData
  } catch {
    capacityError.value = true
  }
}

async function fetchUsers(): Promise<void> {
  usersLoading.value = true
  usersError.value = false
  try {
    usersData.value = await apiClient.get<UserRow[]>('/api/users')
  } catch {
    usersError.value = true
  } finally {
    usersLoading.value = false
  }
}

// ── Backup ────────────────────────────────────────────────────────────────────

async function downloadBackupScript(): Promise<void> {
  backupDownloading.value = true
  backupError.value = false
  try {
    const res = await fetch('/api/backup/script', { credentials: 'include' })
    if (!res.ok) throw new Error()
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'backup.sh'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  } catch {
    backupError.value = true
  } finally {
    backupDownloading.value = false
  }
}

// ── User actions ──────────────────────────────────────────────────────────────

async function toggleRole(user: UserRow): Promise<void> {
  roleChanging.value = user.id
  resetPwFeedback.value = null
  const newRole = user.role === 'admin' ? 'member' : 'admin'
  try {
    const updated = await apiClient.patch<UserRow>(`/api/users/${user.id}/role`, { role: newRole })
    const idx = usersData.value.findIndex((u) => u.id === user.id)
    if (idx !== -1) usersData.value[idx] = updated
  } catch {
    // silently ignore – row will keep old role
  } finally {
    roleChanging.value = null
  }
}

function openReset(userId: string): void {
  openResetPw.value = openResetPw.value === userId ? null : userId
  resetPwValue.value = ''
  resetPwFeedback.value = null
}

async function saveResetPw(user: UserRow): Promise<void> {
  if (resetPwValue.value.length < 8) {
    resetPwFeedback.value = { id: user.id, msg: t('settings.usersResetPwTooShort'), ok: false }
    return
  }
  resetPwSaving.value = true
  resetPwFeedback.value = null
  try {
    await apiClient.patch(`/api/users/${user.id}/password`, { new_password: resetPwValue.value })
    resetPwFeedback.value = { id: user.id, msg: t('settings.usersResetPwSuccess'), ok: true }
    openResetPw.value = null
    resetPwValue.value = ''
  } catch {
    resetPwFeedback.value = { id: user.id, msg: t('settings.usersResetPwError'), ok: false }
  } finally {
    resetPwSaving.value = false
  }
}

function requestDeactivate(userId: string): void {
  if (confirmDeactivate.value === userId) {
    performDeactivate(userId)
  } else {
    confirmDeactivate.value = userId
    resetPwFeedback.value = null
  }
}

async function performDeactivate(userId: string): Promise<void> {
  try {
    await apiClient.delete(`/api/users/${userId}`)
    usersData.value = usersData.value.filter((u) => u.id !== userId)
  } catch {
    // ignore
  } finally {
    confirmDeactivate.value = null
  }
}

async function createUser(): Promise<void> {
  newUserFeedback.value = null
  if (!newUsername.value.trim() || !newPassword.value) return
  newUserSaving.value = true
  try {
    const created = await apiClient.post<UserRow>('/api/users', {
      username: newUsername.value.trim(),
      password: newPassword.value,
      role: newRole.value,
    })
    usersData.value.push(created)
    newUserFeedback.value = { msg: t('settings.usersCreateSuccess'), ok: true }
    newUsername.value = ''
    newPassword.value = ''
    newRole.value = 'member'
    showNewUser.value = false
  } catch (e) {
    const msg =
      e instanceof ApiError && e.status === 409
        ? t('settings.usersCreateTaken')
        : t('settings.usersCreateError')
    newUserFeedback.value = { msg, ok: false }
  } finally {
    newUserSaving.value = false
  }
}

// ── Lifecycle ─────────────────────────────────────────────────────────────────

onMounted(async () => {
  await fetchCapacity()
})

watch(activeSection, (val) => {
  if (val === 'users' && usersData.value.length === 0 && !usersLoading.value) {
    fetchUsers()
  }
})

// ── Backdrop ──────────────────────────────────────────────────────────────────

function handleBackdropClick(e: MouseEvent): void {
  if ((e.target as HTMLElement).classList.contains('settings-modal__backdrop')) {
    closeSettings()
  }
}
</script>

<template>
  <Teleport to="body">
    <div class="settings-modal__backdrop" @click="handleBackdropClick">
      <div class="settings-modal" role="dialog" aria-modal="true" :aria-label="t('settings.title')">

        <!-- Header -->
        <div class="settings-modal__header">
          <span class="settings-modal__title">
            <Icon icon="mdi:cog-outline" width="16" height="16" />
            Einstellungen
          </span>
          <button class="settings-modal__close" :aria-label="t('settings.close')" @click="closeSettings">
            <Icon icon="mdi:close" width="16" height="16" />
          </button>
        </div>

        <!-- Body -->
        <div class="settings-modal__body">

          <!-- Left: nav -->
          <nav class="settings-nav">

            <button
              class="settings-nav__item"
              :class="{ 'settings-nav__item--active': activeSection === 'language' }"
              @click="activeSection = 'language'"
            >
              <Icon icon="mdi:translate" width="15" height="15" class="settings-nav__icon" />
              <span>{{ t('settings.language') }}</span>
            </button>

            <button
              class="settings-nav__item"
              :class="{ 'settings-nav__item--active': activeSection === 'date' }"
              @click="activeSection = 'date'"
            >
              <Icon icon="mdi:calendar-text-outline" width="15" height="15" class="settings-nav__icon" />
              <span>{{ t('settings.date') }}</span>
            </button>

            <button
              class="settings-nav__item"
              :class="{ 'settings-nav__item--active': activeSection === 'storage' }"
              @click="activeSection = 'storage'"
            >
              <Icon icon="mdi:harddisk" width="15" height="15" class="settings-nav__icon" />
              <span>{{ t('settings.storage') }}</span>
            </button>

            <button
              class="settings-nav__item"
              :class="{
                'settings-nav__item--active': activeSection === 'backup',
                'settings-nav__item--locked': !auth.isAdmin,
              }"
              :disabled="!auth.isAdmin"
              :title="!auth.isAdmin ? t('settings.usersAdminOnly') : undefined"
              @click="auth.isAdmin && (activeSection = 'backup')"
            >
              <Icon icon="mdi:backup-restore" width="15" height="15" class="settings-nav__icon" />
              <span>{{ t('settings.backup') }}</span>
              <Icon
                v-if="!auth.isAdmin"
                icon="mdi:lock-outline"
                width="12"
                height="12"
                class="settings-nav__lock"
              />
            </button>

            <button
              class="settings-nav__item"
              :class="{
                'settings-nav__item--active': activeSection === 'users',
                'settings-nav__item--locked': !auth.isAdmin,
              }"
              :disabled="!auth.isAdmin"
              :title="!auth.isAdmin ? t('settings.usersAdminOnly') : undefined"
              @click="auth.isAdmin && (activeSection = 'users')"
            >
              <Icon icon="mdi:account-group-outline" width="15" height="15" class="settings-nav__icon" />
              <span>{{ t('settings.users') }}</span>
              <Icon
                v-if="!auth.isAdmin"
                icon="mdi:lock-outline"
                width="12"
                height="12"
                class="settings-nav__lock"
              />
            </button>

          </nav>

          <!-- Right: content -->
          <div class="settings-view">

            <!-- ── Language ─────────────────────────────────────────────── -->
            <template v-if="activeSection === 'language'">
              <h2 class="settings-view__heading">{{ t('settings.language') }}</h2>
              <p class="settings-view__desc">{{ t('settings.languageDesc') }}</p>

              <div class="lang-options">
                <button
                  v-for="lang in LANGUAGES"
                  :key="lang.code"
                  class="lang-option"
                  :class="{ 'lang-option--active': currentLocale === lang.code }"
                  @click="selectLocale(lang.code)"
                >
                  <span class="lang-option__flag">{{ lang.flag }}</span>
                  <span class="lang-option__label">{{ t(lang.labelKey) }}</span>
                  <Icon
                    v-if="currentLocale === lang.code"
                    icon="mdi:check"
                    width="15"
                    height="15"
                    class="lang-option__check"
                  />
                </button>
              </div>
            </template>

            <!-- ── Date format ──────────────────────────────────────────── -->
            <template v-else-if="activeSection === 'date'">
              <h2 class="settings-view__heading">{{ t('settings.date') }}</h2>
              <p class="settings-view__desc">{{ t('settings.dateDesc') }}</p>

              <div class="lang-options">
                <button
                  v-for="fmt in DATE_FORMAT_OPTIONS"
                  :key="fmt"
                  class="lang-option"
                  :class="{ 'lang-option--active': dateFormatPref === fmt }"
                  :disabled="dateFormatSaving !== null"
                  @click="selectDateFormat(fmt)"
                >
                  <span class="date-option__token">{{ fmt }}</span>
                  <span class="date-option__sample">{{ dateFormatSample(fmt) }}</span>
                  <Icon
                    v-if="dateFormatPref === fmt"
                    icon="mdi:check"
                    width="15"
                    height="15"
                    class="lang-option__check"
                  />
                </button>
              </div>
              <p v-if="dateFormatError" class="settings-view__desc feedback--err">
                {{ t('settings.dateSaveError') }}
              </p>
            </template>

            <!-- ── Storage ──────────────────────────────────────────────── -->
            <template v-else-if="activeSection === 'storage'">
              <h2 class="settings-view__heading">{{ t('settings.storage') }}</h2>

              <div v-if="capacityError" class="settings-view__error">
                <Icon icon="mdi:alert-circle-outline" width="16" height="16" />
                {{ t('settings.capacityLoadError') }}
              </div>

              <div v-else class="settings-view__cards">

                <div class="capacity-card capacity-card--free">
                  <div class="capacity-card__icon">
                    <Icon icon="mdi:harddisk" width="22" height="22" />
                  </div>
                  <div class="capacity-card__body">
                    <span class="capacity-card__label">{{ t('settings.capacityLeft') }}</span>
                    <span class="capacity-card__value">
                      <template v-if="capacity">{{ formatBytes(capacity.free_bytes) }}</template>
                      <span v-else class="capacity-card__skeleton" />
                    </span>
                  </div>
                </div>

                <div class="capacity-card capacity-card--used">
                  <div class="capacity-card__icon">
                    <Icon icon="mdi:database-outline" width="22" height="22" />
                  </div>
                  <div class="capacity-card__body">
                    <span class="capacity-card__label">{{ t('settings.capacityUsed') }}</span>
                    <span class="capacity-card__value">
                      <template v-if="capacity">
                        {{ formatBytes(capacity.used_bytes) }}
                        <span class="capacity-card__pct">({{ usedPercent(capacity) }}&thinsp;%)</span>
                      </template>
                      <span v-else class="capacity-card__skeleton" />
                    </span>
                    <div v-if="capacity" class="capacity-bar">
                      <div
                        class="capacity-bar__fill"
                        :style="{ width: usedPercent(capacity) + '%' }"
                        :class="{
                          'capacity-bar__fill--warn':   usedPercent(capacity) >= 70,
                          'capacity-bar__fill--danger': usedPercent(capacity) >= 90,
                        }"
                      />
                    </div>
                  </div>
                </div>

              </div>
            </template>

            <!-- ── Backup ───────────────────────────────────────────────── -->
            <template v-else-if="activeSection === 'backup'">
              <h2 class="settings-view__heading">{{ t('settings.backup') }}</h2>

              <p class="backup-intro">{{ t('settings.backupIntro') }}</p>

              <div class="backup-steps">
                <h3 class="backup-steps__title">{{ t('settings.backupSetupTitle') }}</h3>
                <ol class="backup-steps__list">
                  <li>{{ t('settings.backupStep1') }}</li>
                  <li>{{ t('settings.backupStep2') }}</li>
                  <li>
                    {{ t('settings.backupStep3') }}
                    <table class="backup-vars">
                      <tbody>
                        <tr>
                          <td class="backup-vars__key">REMOTE_HOST</td>
                          <td class="backup-vars__desc">{{ t('settings.backupVarRemoteHostDesc') }}</td>
                        </tr>
                        <tr>
                          <td class="backup-vars__key">REMOTE_USER</td>
                          <td class="backup-vars__desc">{{ t('settings.backupVarRemoteUserDesc') }}</td>
                        </tr>
                        <tr>
                          <td class="backup-vars__key">CAPYBARCA_DIR</td>
                          <td class="backup-vars__desc">{{ t('settings.backupVarCapybarcaDirDesc') }}</td>
                        </tr>
                        <tr>
                          <td class="backup-vars__key">OUTPUT_DIR</td>
                          <td class="backup-vars__desc">{{ t('settings.backupVarOutputDirDesc') }}</td>
                        </tr>
                      </tbody>
                    </table>
                  </li>
                  <li>
                    {{ t('settings.backupStep4') }}
                    <code class="backup-code backup-code--block">chmod +x backup.sh &amp;&amp; ./backup.sh</code>
                  </li>
                </ol>
              </div>

              <div class="backup-restore-note">
                <Icon icon="mdi:information-outline" width="14" height="14" class="backup-restore-note__icon" />
                {{ t('settings.backupRestoreNote') }}
              </div>

              <div class="backup-actions">
                <button
                  class="backup-download-btn"
                  :disabled="backupDownloading"
                  @click="downloadBackupScript"
                >
                  <Icon
                    :icon="backupDownloading ? 'mdi:loading' : 'mdi:download'"
                    width="16"
                    height="16"
                    :class="{ spin: backupDownloading }"
                  />
                  {{ backupDownloading ? t('settings.backupDownloading') : t('settings.backupDownloadBtn') }}
                </button>

                <p v-if="backupError" class="backup-error">
                  <Icon icon="mdi:alert-circle-outline" width="14" height="14" />
                  {{ t('settings.backupDownloadError') }}
                </p>
              </div>
            </template>

            <!-- ── Users ────────────────────────────────────────────────── -->
            <template v-else-if="activeSection === 'users'">
              <h2 class="settings-view__heading">{{ t('settings.users') }}</h2>

              <div v-if="usersLoading" class="users-loading">
                <Icon icon="mdi:loading" width="18" height="18" class="spin" />
              </div>

              <div v-else-if="usersError" class="settings-view__error">
                <Icon icon="mdi:alert-circle-outline" width="16" height="16" />
                {{ t('settings.usersLoadError') }}
              </div>

              <template v-else>

                <div v-if="otherUsers.length === 0 && !showNewUser" class="users-empty">
                  {{ t('settings.usersEmpty') }}
                </div>

                <!-- User rows -->
                <ul class="users-list">
                  <li v-for="user in otherUsers" :key="user.id" class="user-row">

                    <!-- Top line: avatar + name + role + actions -->
                    <div class="user-row__main">
                      <div class="user-avatar">{{ user.username.charAt(0).toUpperCase() }}</div>
                      <span class="user-row__name">{{ user.username }}</span>
                      <span class="user-badge" :class="roleBadgeClass(user.role)">
                        {{ roleLabel(user.role) }}
                      </span>

                      <div class="user-row__actions">
                        <!-- Role toggle -->
                        <button
                          class="user-action-btn"
                          :disabled="roleChanging === user.id"
                          :title="user.role === 'admin' ? t('settings.usersMakeMember') : t('settings.usersMakeAdmin')"
                          @click="toggleRole(user)"
                        >
                          <Icon
                            :icon="user.role === 'admin' ? 'mdi:account-minus-outline' : 'mdi:account-plus-outline'"
                            width="15" height="15"
                          />
                        </button>

                        <!-- PW reset toggle -->
                        <button
                          class="user-action-btn"
                          :class="{ 'user-action-btn--active': openResetPw === user.id }"
                          :title="t('settings.usersResetPw')"
                          @click="openReset(user.id)"
                        >
                          <Icon icon="mdi:key-outline" width="15" height="15" />
                        </button>

                        <!-- Deactivate -->
                        <button
                          class="user-action-btn user-action-btn--danger"
                          :class="{ 'user-action-btn--confirm': confirmDeactivate === user.id }"
                          :title="confirmDeactivate === user.id ? t('settings.usersDeactivateConfirm') : t('settings.usersDeactivate')"
                          @click="requestDeactivate(user.id)"
                          @blur="confirmDeactivate = null"
                        >
                          <Icon
                            :icon="confirmDeactivate === user.id ? 'mdi:check' : 'mdi:account-off-outline'"
                            width="15" height="15"
                          />
                        </button>
                      </div>
                    </div>

                    <!-- Inline PW reset form -->
                    <div v-if="openResetPw === user.id" class="user-row__pw-form">
                      <input
                        v-model="resetPwValue"
                        class="user-row__pw-input"
                        type="password"
                        :placeholder="t('settings.usersResetPwPlaceholder')"
                        autocomplete="new-password"
                        @keydown.enter="saveResetPw(user)"
                        @keydown.esc="openResetPw = null"
                      />
                      <button
                        class="user-row__pw-save"
                        :disabled="resetPwSaving || resetPwValue.length < 8"
                        @click="saveResetPw(user)"
                      >
                        {{ t('settings.usersResetPwSave') }}
                      </button>
                    </div>

                    <!-- Per-row feedback -->
                    <p
                      v-if="resetPwFeedback && resetPwFeedback.id === user.id"
                      class="user-row__feedback"
                      :class="resetPwFeedback.ok ? 'feedback--ok' : 'feedback--err'"
                    >
                      {{ resetPwFeedback.msg }}
                    </p>

                  </li>
                </ul>

                <!-- New user creation -->
                <div class="users-new">
                  <button
                    v-if="!showNewUser"
                    class="users-new__toggle"
                    @click="showNewUser = true; newUserFeedback = null"
                  >
                    <Icon icon="mdi:plus" width="14" height="14" />
                    {{ t('settings.usersNewUserTitle') }}
                  </button>

                  <div v-else class="users-new__form">
                    <h3 class="users-new__title">{{ t('settings.usersNewUserTitle') }}</h3>
                    <div class="users-new__fields">
                      <input
                        v-model="newUsername"
                        class="users-new__input"
                        type="text"
                        :placeholder="t('settings.usersNewUsernamePlaceholder')"
                        autocomplete="off"
                      />
                      <input
                        v-model="newPassword"
                        class="users-new__input"
                        type="password"
                        :placeholder="t('settings.usersNewPasswordPlaceholder')"
                        autocomplete="new-password"
                      />
                      <p v-if="newPassword && newPassword.length < 8" class="users-new__hint">
                        Mind. 8 Zeichen erforderlich
                      </p>
                      <select v-model="newRole" class="users-new__select">
                        <option value="member">{{ t('settings.usersRoleMember') }}</option>
                        <option value="admin">{{ t('settings.usersRoleAdmin') }}</option>
                      </select>
                    </div>
                    <p
                      v-if="newUserFeedback"
                      class="user-row__feedback"
                      :class="newUserFeedback.ok ? 'feedback--ok' : 'feedback--err'"
                    >
                      {{ newUserFeedback.msg }}
                    </p>
                    <div class="users-new__footer">
                      <button class="users-new__cancel" @click="showNewUser = false">
                        Abbrechen
                      </button>
                      <button
                        class="users-new__save"
                        :disabled="newUserSaving || !newUsername.trim() || newPassword.length < 8"
                        @click="createUser"
                      >
                        {{ newUserSaving ? '...' : t('settings.usersCreate') }}
                      </button>
                    </div>
                  </div>
                </div>

              </template>
            </template>

          </div>
        </div>

      </div>
    </div>
  </Teleport>
</template>

<style scoped>
/* ── Backdrop ────────────────────────────────────────────────────────────── */
.settings-modal__backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  backdrop-filter: blur(2px);
}

/* ── Dialog ──────────────────────────────────────────────────────────────── */
.settings-modal {
  display: flex;
  flex-direction: column;
  width: min(820px, 92vw);
  height: min(560px, 88vh);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  overflow: hidden;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.35);
}

/* ── Header ──────────────────────────────────────────────────────────────── */
.settings-modal__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 1rem;
  height: 44px;
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.settings-modal__title {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--color-text);
}

.settings-modal__close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 5px;
  background: none;
  cursor: pointer;
  color: var(--color-text-muted);
  transition: background 0.12s, color 0.12s;
}

.settings-modal__close:hover {
  background: var(--color-hover);
  color: var(--color-text);
}

/* ── Body ────────────────────────────────────────────────────────────────── */
.settings-modal__body {
  display: flex;
  flex: 1;
  overflow: hidden;
}

/* ── Nav ─────────────────────────────────────────────────────────────────── */
.settings-nav {
  width: 190px;
  flex-shrink: 0;
  border-right: 1px solid var(--color-border);
  padding: 0.5rem 0.375rem;
  overflow-y: auto;
  background: var(--color-sidebar-bg, var(--color-surface));
}

.settings-nav__item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  height: 32px;
  padding: 0 10px;
  border: none;
  border-radius: 5px;
  background: none;
  cursor: pointer;
  font-size: 0.8125rem;
  color: var(--color-text-muted);
  text-align: left;
  transition: background 0.1s, color 0.1s;
}

.settings-nav__item:hover:not(.settings-nav__item--locked) {
  background: var(--color-hover);
  color: var(--color-text);
}

.settings-nav__item--active {
  background: var(--color-active);
  color: var(--color-text);
  font-weight: 500;
}

.settings-nav__item--locked {
  opacity: 0.45;
  cursor: not-allowed;
}

.settings-nav__icon {
  flex-shrink: 0;
  opacity: 0.75;
}

.settings-nav__item--active .settings-nav__icon {
  opacity: 1;
}

.settings-nav__lock {
  margin-left: auto;
  flex-shrink: 0;
  opacity: 0.6;
}

/* ── Content ─────────────────────────────────────────────────────────────── */
.settings-view {
  flex: 1;
  padding: 1.5rem 1.75rem;
  overflow-y: auto;
}

.settings-view__heading {
  margin: 0 0 1.25rem;
  font-size: 1rem;
  font-weight: 600;
  color: var(--color-text);
}

.settings-view__error {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.8125rem;
  color: #e05353;
}

/* ── Capacity cards ──────────────────────────────────────────────────────── */
.settings-view__cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 1rem;
}

.capacity-card {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 1rem 1.125rem;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: var(--color-bg);
  transition: border-color 0.15s;
}

.capacity-card:hover { border-color: var(--color-accent); }

.capacity-card__icon {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: 8px;
  background: color-mix(in srgb, var(--color-accent) 10%, transparent);
  color: var(--color-accent);
}

.capacity-card__body {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
  flex: 1;
}

.capacity-card__label {
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.capacity-card__value {
  font-size: 1.125rem;
  font-weight: 600;
  color: var(--color-text);
  display: flex;
  align-items: baseline;
  gap: 4px;
}

.capacity-card__pct {
  font-size: 0.8125rem;
  font-weight: 400;
  color: var(--color-text-muted);
}

.capacity-card__skeleton {
  display: inline-block;
  width: 80px;
  height: 1.25em;
  border-radius: 4px;
  background: var(--color-border);
  animation: pulse 1.4s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.45; }
}

.capacity-bar {
  margin-top: 6px;
  height: 5px;
  width: 100%;
  border-radius: 3px;
  background: var(--color-border);
  overflow: hidden;
}

.capacity-bar__fill {
  height: 100%;
  border-radius: 3px;
  background: var(--color-accent);
  transition: width 0.4s ease;
}

.capacity-bar__fill--warn   { background: #e0a732; }
.capacity-bar__fill--danger { background: #e05353; }

/* ── Backup section ──────────────────────────────────────────────────────── */
.backup-intro {
  font-size: 0.8125rem;
  color: var(--color-text-muted);
  margin: 0 0 1.25rem;
  line-height: 1.55;
}

.backup-steps {
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: var(--color-bg);
  padding: 1rem 1.125rem;
  margin-bottom: 0.875rem;
}

.backup-steps__title {
  margin: 0 0 0.75rem;
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--color-text);
}

.backup-steps__list {
  margin: 0;
  padding-left: 1.25rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  font-size: 0.8125rem;
  color: var(--color-text);
  line-height: 1.5;
}

.backup-vars {
  margin-top: 0.5rem;
  border-collapse: collapse;
  width: 100%;
}

.backup-vars td {
  padding: 4px 8px 4px 0;
  vertical-align: top;
  font-size: 0.8125rem;
}

.backup-vars__key {
  font-family: ui-monospace, 'Cascadia Code', 'Source Code Pro', Menlo, monospace;
  font-size: 0.75rem;
  color: var(--color-accent);
  white-space: nowrap;
  padding-right: 12px;
}

.backup-vars__desc {
  color: var(--color-text-muted);
  line-height: 1.45;
}

.backup-restore-note {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  font-size: 0.8125rem;
  color: var(--color-text-muted);
  background: var(--color-hover);
  border: 1px solid var(--color-border);
  border-radius: 6px;
  padding: 0.625rem 0.875rem;
  margin-bottom: 1.25rem;
  line-height: 1.5;
}

.backup-restore-note__icon {
  flex-shrink: 0;
  margin-top: 1px;
  opacity: 0.7;
}

.backup-code {
  font-family: ui-monospace, 'Cascadia Code', 'Source Code Pro', Menlo, monospace;
  font-size: 0.75rem;
  background: color-mix(in srgb, var(--color-accent) 10%, transparent);
  color: var(--color-accent);
  padding: 1px 5px;
  border-radius: 3px;
}

.backup-code--block {
  display: block;
  margin-top: 4px;
  padding: 6px 10px;
  border-radius: 5px;
}

.backup-actions {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.backup-download-btn {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 7px 16px;
  font-size: 0.8125rem;
  font-weight: 500;
  font-family: inherit;
  border: none;
  border-radius: 6px;
  background: var(--color-accent);
  color: #fff;
  cursor: pointer;
  transition: opacity 0.12s;
}

.backup-download-btn:hover:not(:disabled) {
  opacity: 0.88;
}

.backup-download-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.backup-error {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  font-size: 0.8125rem;
  color: #e05353;
}

/* ── Users section ───────────────────────────────────────────────────────── */
.users-loading {
  display: flex;
  justify-content: center;
  padding: 2rem;
  color: var(--color-text-muted);
}

.users-empty {
  font-size: 0.8125rem;
  color: var(--color-text-muted);
  margin-bottom: 1rem;
}

.users-list {
  list-style: none;
  margin: 0 0 1rem;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

/* ── User row ────────────────────────────────────────────────────────────── */
.user-row {
  border: 1px solid var(--color-border);
  border-radius: 7px;
  background: var(--color-bg);
  overflow: hidden;
}

.user-row__main {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
}

.user-avatar {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--color-accent);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.75rem;
  font-weight: 700;
  flex-shrink: 0;
}

.user-row__name {
  font-size: 0.875rem;
  font-weight: 500;
  color: var(--color-text);
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-badge {
  font-size: 0.6875rem;
  font-weight: 600;
  padding: 2px 7px;
  border-radius: 20px;
  flex-shrink: 0;
}

.badge--admin {
  background: color-mix(in srgb, var(--color-accent) 15%, transparent);
  color: var(--color-accent);
}

.badge--member {
  background: var(--color-hover);
  color: var(--color-text-muted);
}

.user-row__actions {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-left: auto;
  flex-shrink: 0;
}

.user-action-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid var(--color-border);
  border-radius: 5px;
  background: none;
  cursor: pointer;
  color: var(--color-text-muted);
  transition: background 0.1s, color 0.1s, border-color 0.1s;
  padding: 0;
}

.user-action-btn:hover:not(:disabled) {
  background: var(--color-hover);
  color: var(--color-text);
}

.user-action-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.user-action-btn--active {
  background: color-mix(in srgb, var(--color-accent) 12%, transparent);
  border-color: var(--color-accent);
  color: var(--color-accent);
}

.user-action-btn--danger:hover:not(:disabled) {
  background: color-mix(in srgb, #e05353 10%, transparent);
  border-color: #e05353;
  color: #e05353;
}

.user-action-btn--confirm {
  background: color-mix(in srgb, #e05353 12%, transparent);
  border-color: #e05353;
  color: #e05353;
}

/* ── PW reset form ───────────────────────────────────────────────────────── */
.user-row__pw-form {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px 8px;
  border-top: 1px solid var(--color-border);
  background: var(--color-sidebar-bg, var(--color-surface));
}

.user-row__pw-input {
  flex: 1;
  padding: 5px 8px;
  font-size: 0.8125rem;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  background: var(--color-bg);
  color: var(--color-text);
  font-family: inherit;
}

.user-row__pw-input:focus {
  outline: none;
  border-color: var(--color-accent);
}

.user-row__pw-save {
  padding: 5px 12px;
  font-size: 0.8125rem;
  border: none;
  border-radius: 4px;
  background: var(--color-accent);
  color: #fff;
  cursor: pointer;
  font-family: inherit;
  font-weight: 500;
  transition: opacity 0.12s;
  white-space: nowrap;
}

.user-row__pw-save:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

/* ── Per-row feedback ────────────────────────────────────────────────────── */
.user-row__feedback {
  font-size: 0.75rem;
  margin: 0;
  padding: 4px 10px 6px;
}

.feedback--ok  { color: #4caf7d; }
.feedback--err { color: #e05353; }

/* ── New user ────────────────────────────────────────────────────────────── */
.users-new {
  margin-top: 0.5rem;
}

.users-new__toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  background: none;
  border: 1px dashed var(--color-border);
  border-radius: 6px;
  padding: 7px 12px;
  font-size: 0.8125rem;
  color: var(--color-text-muted);
  cursor: pointer;
  width: 100%;
  transition: background 0.1s, color 0.1s;
}

.users-new__toggle:hover {
  background: var(--color-hover);
  color: var(--color-text);
}

.users-new__form {
  border: 1px solid var(--color-border);
  border-radius: 7px;
  background: var(--color-bg);
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.users-new__title {
  margin: 0;
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--color-text);
}

.users-new__fields {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.users-new__input,
.users-new__select {
  padding: 6px 8px;
  font-size: 0.8125rem;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  background: var(--color-surface);
  color: var(--color-text);
  font-family: inherit;
}

.users-new__input:focus,
.users-new__select:focus {
  outline: none;
  border-color: var(--color-accent);
}

.users-new__footer {
  display: flex;
  justify-content: flex-end;
  gap: 6px;
}

.users-new__cancel {
  padding: 5px 12px;
  font-size: 0.8125rem;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  background: none;
  color: var(--color-text-muted);
  cursor: pointer;
  font-family: inherit;
  transition: background 0.1s;
}

.users-new__cancel:hover {
  background: var(--color-hover);
}

.users-new__hint {
  font-size: 0.75rem;
  color: #e0a732;
  margin: 0;
}

.users-new__save {
  padding: 5px 14px;
  font-size: 0.8125rem;
  border: none;
  border-radius: 4px;
  background: var(--color-accent);
  color: #fff;
  cursor: pointer;
  font-family: inherit;
  font-weight: 500;
  transition: opacity 0.12s;
}

.users-new__save:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

/* ── Animations ──────────────────────────────────────────────────────────── */
.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}

/* ── Language section ────────────────────────────────────────────────────── */

.lang-options {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 0.5rem;
  max-width: 320px;
}

.lang-option {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border: 1px solid var(--color-border);
  border-radius: 7px;
  background: var(--color-surface);
  cursor: pointer;
  font-size: 0.875rem;
  color: var(--color-text);
  text-align: left;
  transition: background 0.1s, border-color 0.1s;
}

.lang-option:hover {
  background: var(--color-hover);
}

.lang-option--active {
  border-color: var(--color-accent);
  background: var(--color-accent-subtle);
}

.lang-option__flag {
  font-size: 1.25rem;
  line-height: 1;
  flex-shrink: 0;
}

.lang-option__label {
  flex: 1;
  font-weight: 500;
}

.lang-option__check {
  color: var(--color-accent);
  flex-shrink: 0;
}

/* ── Date format section ─────────────────────────────────────────────────── */

.lang-option:disabled {
  opacity: 0.6;
  cursor: default;
}

.date-option__token {
  flex: 1;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}

.date-option__sample {
  flex-shrink: 0;
  font-size: 0.8125rem;
  color: var(--color-text-muted);
  font-variant-numeric: tabular-nums;
}
</style>

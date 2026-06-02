<script setup lang="ts">
/**
 * PermissionsSection
 *
 * Inline permissions panel used as a section inside PageSettingsModal.
 *
 * Displays the current access mode for the block and allows the owner (or
 * any admin) to change it.  Available modes:
 *
 *   inherit   – no explicit setting; effective permission comes from the
 *               nearest ancestor that has an explicit row (default)
 *   everyone  – all authenticated users may read this block
 *   private   – only the owner may read this block
 *   whitelist – owner + explicitly listed users may read this block
 *
 * When mode is 'whitelist', a user selector is shown that lets the owner
 * add / remove individual users from the grant list.
 */
import { ref, computed, watch, onMounted } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { apiClient } from '@/api/client'

// ── Props ─────────────────────────────────────────────────────────────────────

const props = defineProps<{
  blockId: string
}>()

// ── Dependencies ──────────────────────────────────────────────────────────────

const { t } = useI18n()

// ── Types ─────────────────────────────────────────────────────────────────────

type PermMode = 'inherit' | 'everyone' | 'private' | 'whitelist'

interface PermissionData {
  block_id: string
  mode: PermMode
  owner_id: string | null
  grants: string[]
  effective_mode: string
  inherited_from_id: string | null
  can_edit: boolean
  warn_accessibility: boolean
}

interface UserName {
  id: string
  username: string
}

// ── State ─────────────────────────────────────────────────────────────────────

const loading    = ref(false)
const saving     = ref(false)
const error      = ref<string | null>(null)
const linkCopied = ref(false)

const data      = ref<PermissionData | null>(null)
const mode      = ref<PermMode>('inherit')
const grants    = ref<string[]>([])

const allUsers  = ref<UserName[]>([])
const ownerName = computed(() => {
  if (!data.value?.owner_id) return t('permissions.noOwner')
  return allUsers.value.find(u => u.id === data.value!.owner_id)?.username
    ?? data.value.owner_id
})

// can_edit is resolved server-side: true when the requesting user is admin
// or owns the block. Avoids exposing user IDs on the frontend auth store.
const isOwnerOrAdmin = computed(() => data.value?.can_edit ?? false)

// ── Mode options ──────────────────────────────────────────────────────────────

const MODE_OPTIONS: { value: PermMode; labelKey: string; descKey: string; icon: string }[] = [
  {
    value: 'inherit',
    labelKey: 'permissions.modeInherit',
    descKey: 'permissions.modeInheritDesc',
    icon: 'mdi:arrow-up-circle-outline',
  },
  {
    value: 'everyone',
    labelKey: 'permissions.modeEveryone',
    descKey: 'permissions.modeEveryoneDesc',
    icon: 'mdi:earth',
  },
  {
    value: 'private',
    labelKey: 'permissions.modePrivate',
    descKey: 'permissions.modePrivateDesc',
    icon: 'mdi:lock-outline',
  },
  {
    value: 'whitelist',
    labelKey: 'permissions.modeWhitelist',
    descKey: 'permissions.modeWhitelistDesc',
    icon: 'mdi:account-multiple-outline',
  },
]

// ── Whitelist management ──────────────────────────────────────────────────────

const addUserId = ref<string>('')

const grantedUsers = computed<UserName[]>(() =>
  allUsers.value.filter(u => grants.value.includes(u.id))
)

const ungrantedUsers = computed<UserName[]>(() =>
  allUsers.value.filter(u => !grants.value.includes(u.id))
)

function addGrant(): void {
  if (!addUserId.value || grants.value.includes(addUserId.value)) return
  grants.value = [...grants.value, addUserId.value]
  addUserId.value = ''
}

function removeGrant(userId: string): void {
  grants.value = grants.value.filter(id => id !== userId)
}

// ── Data loading ──────────────────────────────────────────────────────────────

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const [permData, usersData] = await Promise.all([
      apiClient.get<PermissionData>(`/api/blocks/${props.blockId}/permissions`),
      apiClient.get<UserName[]>('/api/users/names'),
    ])
    data.value  = permData
    mode.value  = permData.mode
    grants.value = [...permData.grants]
    allUsers.value = usersData
  } catch {
    error.value = t('permissions.loadError')
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(() => props.blockId, load)

// ── Copy link ─────────────────────────────────────────────────────────────────

function copyLink(): void {
  navigator.clipboard.writeText(window.location.href).then(() => {
    linkCopied.value = true
    setTimeout(() => { linkCopied.value = false }, 2000)
  })
}

// ── Save ──────────────────────────────────────────────────────────────────────

async function save(): Promise<void> {
  saving.value = true
  error.value  = null
  try {
    const updated = await apiClient.put<PermissionData>(
      `/api/blocks/${props.blockId}/permissions`,
      { mode: mode.value, grants: grants.value },
    )
    data.value = updated
    mode.value = updated.mode
    grants.value = [...updated.grants]
  } catch {
    error.value = t('permissions.saveError')
  } finally {
    saving.value = false
  }
}

const isDirty = computed(() => {
  if (!data.value) return false
  if (mode.value !== data.value.mode) return true
  const sorted = (a: string[]) => [...a].sort().join(',')
  return sorted(grants.value) !== sorted(data.value.grants)
})
</script>

<template>
  <div class="perm">
    <!-- Loading -->
    <div v-if="loading" class="perm__state">
      <span class="perm__spinner" />
    </div>

    <!-- Error -->
    <div v-else-if="error" class="perm__error">
      <Icon icon="mdi:alert-circle-outline" width="14" height="14" />
      {{ error }}
    </div>

    <template v-else-if="data">

      <!-- Owner row -->
      <div class="perm__section">
        <p class="perm__section-title">{{ t('permissions.ownerLabel') }}</p>
        <div class="perm__owner-row">
          <Icon icon="mdi:account-circle-outline" width="15" height="15" class="perm__owner-icon" />
          <span class="perm__owner-name">{{ ownerName }}</span>
        </div>
      </div>

      <!-- Effective permission (shown when inherited) -->
      <div v-if="mode === 'inherit' && data.effective_mode" class="perm__inherited-hint">
        <Icon icon="mdi:information-outline" width="13" height="13" />
        {{ t('permissions.inheritedAs', { mode: t(`permissions.mode${data.effective_mode.charAt(0).toUpperCase() + data.effective_mode.slice(1)}`) }) }}
      </div>

      <!-- Accessibility warning: block is more permissive than its ancestors -->
      <div v-if="data.warn_accessibility" class="perm__warn">
        <div class="perm__warn-header">
          <Icon icon="mdi:alert-outline" width="14" height="14" class="perm__warn-icon" />
          <span>{{ t('permissions.warnAccessibilityTitle') }}</span>
        </div>
        <p class="perm__warn-body">{{ t('permissions.warnAccessibilityBody') }}</p>
        <button class="perm__copy-link-btn" @click="copyLink">
          <Icon
            :icon="linkCopied ? 'mdi:check' : 'mdi:link-variant'"
            width="13"
            height="13"
          />
          {{ linkCopied ? t('permissions.linkCopied') : t('permissions.copyLink') }}
        </button>
      </div>

      <!-- Mode selector -->
      <div class="perm__section">
        <p class="perm__section-title">{{ t('permissions.accessLabel') }}</p>
        <div class="perm__modes">
          <button
            v-for="opt in MODE_OPTIONS"
            :key="opt.value"
            class="perm__mode-btn"
            :class="{ 'perm__mode-btn--active': mode === opt.value }"
            :disabled="!isOwnerOrAdmin"
            @click="mode = opt.value"
          >
            <Icon :icon="opt.icon" width="15" height="15" class="perm__mode-icon" />
            <div class="perm__mode-text">
              <span class="perm__mode-label">{{ t(opt.labelKey) }}</span>
              <span class="perm__mode-desc">{{ t(opt.descKey) }}</span>
            </div>
          </button>
        </div>
      </div>

      <!-- Whitelist editor -->
      <div v-if="mode === 'whitelist'" class="perm__section">
        <p class="perm__section-title">{{ t('permissions.grantedUsers') }}</p>

        <!-- Current grants -->
        <div v-if="grantedUsers.length > 0" class="perm__grant-list">
          <div
            v-for="user in grantedUsers"
            :key="user.id"
            class="perm__grant-row"
          >
            <Icon icon="mdi:account-outline" width="13" height="13" class="perm__grant-icon" />
            <span class="perm__grant-name">{{ user.username }}</span>
            <button
              v-if="isOwnerOrAdmin"
              class="perm__grant-remove"
              :title="t('permissions.removeGrant')"
              @click="removeGrant(user.id)"
            >
              <Icon icon="mdi:close" width="12" height="12" />
            </button>
          </div>
        </div>
        <p v-else class="perm__empty-hint">{{ t('permissions.noGrants') }}</p>

        <!-- Add user -->
        <div v-if="isOwnerOrAdmin" class="perm__add-row">
          <select v-model="addUserId" class="perm__add-select">
            <option value="">{{ t('permissions.selectUser') }}</option>
            <option
              v-for="user in ungrantedUsers"
              :key="user.id"
              :value="user.id"
            >
              {{ user.username }}
            </option>
          </select>
          <button
            class="perm__add-btn"
            :disabled="!addUserId"
            @click="addGrant"
          >
            {{ t('permissions.addGrant') }}
          </button>
        </div>
      </div>

      <!-- Direct link -->
      <div class="perm__link-row">
        <button class="perm__copy-link-btn perm__copy-link-btn--standalone" @click="copyLink">
          <Icon
            :icon="linkCopied ? 'mdi:check' : 'mdi:link-variant'"
            width="13"
            height="13"
          />
          {{ linkCopied ? t('permissions.linkCopied') : t('permissions.copyLink') }}
        </button>
      </div>

      <!-- Save button -->
      <div v-if="isOwnerOrAdmin" class="perm__actions">
        <button
          class="perm__save-btn"
          :disabled="!isDirty || saving"
          @click="save"
        >
          <span v-if="saving" class="perm__spinner perm__spinner--small" />
          {{ saving ? t('permissions.saving') : t('permissions.save') }}
        </button>
      </div>

      <!-- Read-only notice for non-owners -->
      <p v-else class="perm__readonly-notice">
        <Icon icon="mdi:lock-outline" width="13" height="13" />
        {{ t('permissions.readonlyNotice') }}
      </p>

    </template>
  </div>
</template>

<style scoped>
.perm {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

/* ── State ───────────────────────────────────────────────────────────────── */
.perm__state {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1rem 0;
}

.perm__spinner {
  display: inline-block;
  width: 18px;
  height: 18px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-accent);
  border-radius: 50%;
  animation: perm-spin 0.6s linear infinite;
}

.perm__spinner--small {
  width: 12px;
  height: 12px;
  margin-right: 6px;
}

@keyframes perm-spin {
  to { transform: rotate(360deg); }
}

.perm__error {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--color-danger, #e55);
  font-size: 0.8125rem;
}

/* ── Section ─────────────────────────────────────────────────────────────── */
.perm__section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.perm__section-title {
  font-size: 0.72rem;
  font-weight: 600;
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin: 0;
}

/* ── Owner ───────────────────────────────────────────────────────────────── */
.perm__owner-row {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 0.875rem;
  color: var(--color-text);
}

.perm__owner-icon {
  color: var(--color-text-muted);
  flex-shrink: 0;
}

/* ── Inherited hint ──────────────────────────────────────────────────────── */
.perm__inherited-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.775rem;
  color: var(--color-text-muted);
  background: var(--color-hover);
  border-radius: 5px;
  padding: 6px 10px;
}

/* ── Mode buttons ────────────────────────────────────────────────────────── */
.perm__modes {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.perm__mode-btn {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  width: 100%;
  padding: 8px 10px;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: none;
  cursor: pointer;
  text-align: left;
  transition: background 0.1s, border-color 0.1s;
  color: var(--color-text);
}

.perm__mode-btn:hover:not(:disabled) {
  background: var(--color-hover);
}

.perm__mode-btn--active {
  border-color: var(--color-accent);
  background: var(--color-active);
}

.perm__mode-btn:disabled {
  opacity: 0.6;
  cursor: default;
}

.perm__mode-icon {
  flex-shrink: 0;
  margin-top: 1px;
  color: var(--color-text-muted);
}

.perm__mode-btn--active .perm__mode-icon {
  color: var(--color-accent);
}

.perm__mode-text {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.perm__mode-label {
  font-size: 0.8125rem;
  font-weight: 500;
}

.perm__mode-desc {
  font-size: 0.74rem;
  color: var(--color-text-muted);
  line-height: 1.35;
}

/* ── Grant list ──────────────────────────────────────────────────────────── */
.perm__grant-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.perm__grant-row {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 4px 8px;
  border-radius: 5px;
  background: var(--color-hover);
}

.perm__grant-icon {
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.perm__grant-name {
  font-size: 0.8125rem;
  flex: 1;
}

.perm__grant-remove {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border: none;
  border-radius: 4px;
  background: none;
  cursor: pointer;
  color: var(--color-text-muted);
  flex-shrink: 0;
  transition: background 0.1s, color 0.1s;
}

.perm__grant-remove:hover {
  background: var(--color-danger-subtle, rgba(220, 50, 50, 0.12));
  color: var(--color-danger, #e55);
}

.perm__empty-hint {
  font-size: 0.8rem;
  color: var(--color-text-muted);
  margin: 0;
}

/* ── Add user row ────────────────────────────────────────────────────────── */
.perm__add-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.perm__add-select {
  flex: 1;
  height: 30px;
  padding: 0 8px;
  border: 1px solid var(--color-border);
  border-radius: 5px;
  background: var(--color-surface);
  color: var(--color-text);
  font-size: 0.8125rem;
  cursor: pointer;
}

.perm__add-btn {
  height: 30px;
  padding: 0 12px;
  border: none;
  border-radius: 5px;
  background: var(--color-accent);
  color: #fff;
  font-size: 0.8125rem;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
  transition: opacity 0.12s;
}

.perm__add-btn:disabled {
  opacity: 0.4;
  cursor: default;
}

/* ── Actions ─────────────────────────────────────────────────────────────── */
.perm__actions {
  display: flex;
  justify-content: flex-end;
  padding-top: 4px;
}

.perm__save-btn {
  display: flex;
  align-items: center;
  height: 32px;
  padding: 0 16px;
  border: none;
  border-radius: 6px;
  background: var(--color-accent);
  color: #fff;
  font-size: 0.8125rem;
  font-weight: 500;
  cursor: pointer;
  transition: opacity 0.12s;
}

.perm__save-btn:disabled {
  opacity: 0.4;
  cursor: default;
}

/* ── Readonly notice ─────────────────────────────────────────────────────── */
.perm__readonly-notice {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 0.775rem;
  color: var(--color-text-muted);
  margin: 0;
}
/* ── Accessibility warning ───────────────────────────────────────────────── */
.perm__warn {
  border: 1px solid var(--color-warning-border, #b45309);
  border-radius: 7px;
  background: var(--color-warning-subtle, rgba(180, 83, 9, 0.08));
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.perm__warn-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--color-warning, #b45309);
}

.perm__warn-icon {
  flex-shrink: 0;
  color: var(--color-warning, #b45309);
}

.perm__warn-body {
  font-size: 0.775rem;
  color: var(--color-text-muted);
  margin: 0;
  line-height: 1.45;
}

/* ── Copy link button ────────────────────────────────────────────────────── */
.perm__copy-link-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  border: 1px solid var(--color-border);
  border-radius: 5px;
  background: var(--color-surface);
  color: var(--color-text);
  font-size: 0.775rem;
  cursor: pointer;
  align-self: flex-start;
  transition: background 0.1s, border-color 0.1s;
}

.perm__copy-link-btn:hover {
  background: var(--color-hover);
  border-color: var(--color-text-muted);
}

.perm__link-row {
  display: flex;
}

.perm__copy-link-btn--standalone {
  align-self: unset;
}

</style>

<script setup lang="ts">
/**
 * AddSchemaPanel
 *
 * Modal panel for adding a new property schema to a database. Opens as an
 * overlay; emits ``close`` when the user confirms or cancels.
 *
 * Types are grouped into five visual sections:
 *   Standard | File upload | Formatted text | Computed | System (read-only)
 *
 * The name field is pre-filled with the localised label of the selected type
 * and auto-updates on type change — unless the user has already typed
 * something manually.
 *
 * Virtual types
 * -------------
 * ``select_multiple`` is a UI-only entry that maps to type ``select`` with
 * config.mode = 'multiple' at creation time. The database never stores the
 * string "select_multiple".
 */
import { ref, onMounted, nextTick, watch, computed } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { useDatabaseStore } from '@/stores/database'
import { PROPERTY_TYPES } from '@/stores/propertyTypes'

// ── Props / emits ─────────────────────────────────────────────────────────────

const props = defineProps<{
  databaseId: string
}>()

const emit = defineEmits<{
  (e: 'close', newSchemaId?: string): void
}>()

// ── Dependencies ──────────────────────────────────────────────────────────────

const { t } = useI18n()
const dbStore = useDatabaseStore()

// ── Local state ───────────────────────────────────────────────────────────────

const selectedType = ref('text')
const name = ref('')
const userHasEditedName = ref(false)
const error = ref('')
const inputEl = ref<HTMLInputElement | null>(null)

// ── Relation config (only relevant when selectedType === 'relation') ───────────

const relationTargetDbId = ref<string>(props.databaseId)
const relationDirection = ref<'unilateral' | 'bilateral' | 'bilateral_self'>('unilateral')
const relationMirrorName = ref<string>('')
const isRelationType = computed(() => selectedType.value === 'relation')

// bilateral_self forces the target to the current database
const effectiveTargetDbId = computed(() =>
  relationDirection.value === 'bilateral_self' ? props.databaseId : relationTargetDbId.value
)

const allDatabases = computed(() => dbStore.allDatabases)

// ── Auto-name logic ───────────────────────────────────────────────────────────

function labelForType(typeValue: string): string {
  const pt = PROPERTY_TYPES.find(p => p.value === typeValue)
  return pt ? t(pt.labelKey) : ''
}

// Update the name field whenever the selected type changes, but only if the
// user has not manually typed anything yet.
watch(selectedType, async (newType) => {
  if (!userHasEditedName.value) {
    name.value = labelForType(newType)
  }
  if (newType === 'relation' && dbStore.allDatabases.length === 0) {
    await dbStore.fetchAllDatabases()
  }
})

onMounted(async () => {
  // Pre-fill with the default type's label.
  name.value = labelForType(selectedType.value)
  await nextTick()
  inputEl.value?.focus()
  inputEl.value?.select()
})

// ── Grouped types ─────────────────────────────────────────────────────────────

const groups: Array<{ key: string; labelKey: string; types: typeof PROPERTY_TYPES[number][] }> = [
  {
    key: 'standard',
    labelKey: 'db.addSchema.groupStandard',
    types: PROPERTY_TYPES.filter(p => p.group === 'standard'),
  },
  {
    key: 'upload',
    labelKey: 'db.addSchema.groupUpload',
    types: PROPERTY_TYPES.filter(p => p.group === 'upload'),
  },
  {
    key: 'formatted',
    labelKey: 'db.addSchema.groupFormatted',
    types: PROPERTY_TYPES.filter(p => p.group === 'formatted'),
  },
  {
    key: 'computed',
    labelKey: 'db.addSchema.groupComputed',
    types: PROPERTY_TYPES.filter(p => p.group === 'computed'),
  },
  {
    key: 'readonly',
    labelKey: 'db.addSchema.groupReadonly',
    types: PROPERTY_TYPES.filter(p => p.group === 'readonly'),
  },
]

// ── Actions ───────────────────────────────────────────────────────────────────

async function confirm() {
  const trimmed = name.value.trim()
  if (!trimmed) {
    error.value = t('db.addSchema.errorRequired')
    return
  }
  error.value = ''

  // Resolve virtual UI types to their actual backend type + default config.
  let actualType = selectedType.value
  let config: Record<string, unknown> | undefined

  switch (selectedType.value) {
    case 'select_multiple':
      actualType = 'select'
      config = { mode: 'multiple', options: [] }
      break
    case 'select':
      config = { mode: 'single', options: [] }
      break
    case 'id':
      config = { prefix: '', next_id: 1 }
      break
    case 'relation':
      config = {
        target_database_id: effectiveTargetDbId.value,
        direction: relationDirection.value,
        mirror_property_name:
          relationDirection.value === 'bilateral'
            ? relationMirrorName.value.trim() || null
            : null,
      }
      break
  }

  try {
    const schema = await dbStore.createSchema(props.databaseId, {
      name: trimmed,
      type: actualType,
      ...(config !== undefined ? { config } : {}),
      group: 'Standard',
    })
    emit('close', schema.id)
  } catch {
    error.value = t('db.addSchema.errorDuplicate')
  }
}

function cancel() {
  emit('close', undefined)
}
</script>

<template>
  <div class="asp-backdrop" @mousedown.self="cancel">
    <div class="asp">
      <h3 class="asp__title">{{ t('db.addSchema.title') }}</h3>

      <label class="asp__label">{{ t('db.addSchema.nameLabel') }}</label>
      <input
        ref="inputEl"
        v-model="name"
        class="asp__input"
        :placeholder="t('db.addSchema.namePlaceholder')"
        @input="userHasEditedName = true"
        @keydown.enter.prevent="confirm"
        @keydown.escape.prevent="cancel"
      />
      <span v-if="error" class="asp__error">{{ error }}</span>

      <label class="asp__label">{{ t('db.addSchema.typeLabel') }}</label>

      <div class="asp__groups">
        <div v-for="group in groups" :key="group.key" class="asp__group">
          <span class="asp__group-label">{{ t(group.labelKey) }}</span>
          <div class="asp__type-grid">
            <button
              v-for="pt in group.types"
              :key="pt.value"
              class="asp__type-btn"
              :class="{
                'asp__type-btn--active': selectedType === pt.value,
                'asp__type-btn--readonly': pt.readonly,
              }"
              @click="selectedType = pt.value"
            >
              <Icon :icon="pt.icon" width="14" height="14" />
              {{ t(pt.labelKey) }}
              <Icon
                v-if="pt.readonly"
                icon="mdi:lock-outline"
                width="11"
                height="11"
                class="asp__lock-icon"
              />
            </button>
          </div>
        </div>
      </div>

      <!-- ── Relation config (shown only when relation type is selected) ───── -->
      <template v-if="isRelationType">
        <div v-if="relationDirection !== 'bilateral_self'" class="asp__rel-section">
          <label class="asp__label">{{ t('db.settings.relationTarget') }}</label>
          <select v-model="relationTargetDbId" class="asp__select">
            <option :value="databaseId">{{ t('db.settings.relationSelf') }}</option>
            <option
              v-for="db in allDatabases.filter(d => d.id !== databaseId)"
              :key="db.id"
              :value="db.id"
            >
              {{ db.title || t('main.untitled') }}
            </option>
          </select>
        </div>

        <div class="asp__rel-section">
          <label class="asp__label">{{ t('db.settings.relationDirection') }}</label>
          <div class="asp__toggle-group">
            <button
              class="asp__toggle-btn"
              :class="{ 'asp__toggle-btn--active': relationDirection === 'unilateral' }"
              @click="relationDirection = 'unilateral'"
            >
              <Icon icon="mdi:arrow-right" width="14" height="14" />
              {{ t('db.settings.relationUnilateral') }}
            </button>
            <button
              class="asp__toggle-btn"
              :class="{ 'asp__toggle-btn--active': relationDirection === 'bilateral' }"
              @click="relationDirection = 'bilateral'"
            >
              <Icon icon="mdi:arrow-left-right" width="14" height="14" />
              {{ t('db.settings.relationBilateral') }}
            </button>
            <button
              class="asp__toggle-btn"
              :class="{ 'asp__toggle-btn--active': relationDirection === 'bilateral_self' }"
              @click="relationDirection = 'bilateral_self'"
            >
              <Icon icon="mdi:arrow-u-left-top" width="14" height="14" />
              {{ t('db.settings.relationBilateralSelf') }}
            </button>
          </div>
        </div>

        <div v-if="relationDirection === 'bilateral'" class="asp__rel-section">
          <label class="asp__label">{{ t('db.settings.relationMirrorName') }}</label>
          <input
            v-model="relationMirrorName"
            class="asp__input"
            :placeholder="t('db.settings.relationMirrorPlaceholder')"
          />
          <p class="asp__hint">{{ t('db.settings.relationMirrorHint') }}</p>
        </div>
      </template>

      <div class="asp__actions">
        <button class="asp__btn asp__btn--ghost" @click="cancel">
          {{ t('actions.cancel') }}
        </button>
        <button class="asp__btn asp__btn--primary" @click="confirm">
          {{ t('db.addSchema.confirm') }}
        </button>
      </div>
    </div>
  </div>
</template>
<style scoped>
.asp-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}

.asp {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 24px;
  width: 420px;
  max-height: 86vh;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.asp__title {
  font-size: 1rem;
  font-weight: 600;
  margin: 0 0 4px;
}

.asp__label {
  font-size: 0.72rem;
  font-weight: 600;
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.asp__input {
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 4px;
  padding: 8px 10px;
  font-size: 0.875rem;
  color: var(--color-text);
  outline: none;
  transition: border-color 0.15s;
}

.asp__input:focus {
  border-color: var(--color-accent);
}

.asp__error {
  font-size: 0.75rem;
  color: #e05555;
}

/* ── Groups ───────────────────────────────────────────────────────────────── */
.asp__groups {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.asp__group {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.asp__group-label {
  font-size: 0.68rem;
  font-weight: 600;
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.asp__type-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 5px;
}

.asp__type-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 10px;
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.8rem;
  color: var(--color-text-muted);
  transition: border-color 0.15s, color 0.15s, background 0.15s;
  text-align: left;
}

.asp__type-btn:hover {
  border-color: var(--color-accent);
  color: var(--color-text);
}

.asp__type-btn--active {
  border-color: var(--color-accent);
  background: var(--color-accent-subtle);
  color: var(--color-text);
}

.asp__type-btn--readonly {
  opacity: 0.85;
}

.asp__lock-icon {
  margin-left: auto;
  opacity: 0.5;
  flex-shrink: 0;
}

/* ── Actions ──────────────────────────────────────────────────────────────── */
.asp__actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 6px;
}

.asp__btn {
  padding: 7px 14px;
  border-radius: 5px;
  border: none;
  cursor: pointer;
  font-size: 0.85rem;
  font-weight: 500;
  transition: background 0.15s, color 0.15s;
}

.asp__btn--ghost {
  background: transparent;
  color: var(--color-text-muted);
  border: 1px solid var(--color-border);
}

.asp__btn--ghost:hover {
  background: var(--color-hover);
  color: var(--color-text);
}

.asp__btn--primary {
  background: var(--color-accent);
  color: #fff;
}

.asp__btn--primary:hover {
  filter: brightness(1.1);
}

/* ── Relation config section ──────────────────────────────────────────────── */
.asp__rel-section {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.asp__hint {
  font-size: 0.72rem;
  color: var(--color-text-muted);
  margin: 0;
  line-height: 1.4;
}

.asp__select {
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 4px;
  padding: 7px 10px;
  font-size: 0.875rem;
  color: var(--color-text);
  outline: none;
  cursor: pointer;
  transition: border-color 0.15s;
}

.asp__select:focus {
  border-color: var(--color-accent);
}

.asp__toggle-group {
  display: flex;
  gap: 5px;
}

.asp__toggle-btn {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  padding: 7px 10px;
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.8rem;
  color: var(--color-text-muted);
  transition: border-color 0.15s, color 0.15s, background 0.15s;
}

.asp__toggle-btn:hover {
  border-color: var(--color-accent);
  color: var(--color-text);
}

.asp__toggle-btn--active {
  border-color: var(--color-accent);
  background: var(--color-accent-subtle);
  color: var(--color-text);
}
</style>

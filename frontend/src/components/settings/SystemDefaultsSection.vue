<script setup lang="ts">
/**
 * SystemDefaultsSection
 *
 * Content of the "System defaults" tab of the global settings (admin only).
 *
 * Family tree nuances
 * -------------------
 * The relations of the family set (parents and children, partners,
 * siblings) offer fixed base nuances the system interprets. Admins add
 * nuances of their own here, each next to the base nuance it counts as;
 * every database shows them at once.
 *
 * Every change is saved immediately. Deleting a nuance asks for confirmation
 * with the number of entries using it; the server moves those values to the
 * base nuance.
 */
import { computed, onMounted, ref } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import {
  useKinshipNuancesStore,
  isFreeLabel,
  type KinshipAlias,
  type KinshipNuanceGroup,
} from '@/stores/kinshipNuances'

const { t } = useI18n()
const store = useKinshipNuancesStore()

const loadFailed = ref(false)
const saving = ref(false)
const feedback = ref('')

/** Draft label per row, keyed by ``group/base``. */
const drafts = ref<Record<string, string>>({})

interface PendingDelete {
  group: KinshipNuanceGroup
  alias: KinshipAlias
  count: number | null
}
const pendingDelete = ref<PendingDelete | null>(null)

onMounted(async () => {
  try {
    await store.load()
  } catch {
    loadFailed.value = true
  }
})

interface Row {
  key: string
  base: { id: string; label: string }
  aliases: KinshipAlias[]
}

interface GroupView {
  group: KinshipNuanceGroup
  title: string
  rows: Row[]
}

const GROUP_TITLES: Record<string, string> = {
  parents: 'settings.kinGroupParents',
  partners: 'settings.kinGroupPartners',
  siblings: 'settings.kinGroupSiblings',
}

const groupViews = computed<GroupView[]>(() =>
  store.groups.map((group) => ({
    group,
    title: t(GROUP_TITLES[group.group] ?? group.group),
    rows: group.bases.map((base) => ({
      key: `${group.group}/${base.id}`,
      base,
      aliases: group.aliases.filter((a) => a.base === base.id),
    })),
  })),
)

function withAliases(group: KinshipNuanceGroup, aliases: KinshipAlias[]): KinshipNuanceGroup[] {
  return store.groups.map((g) => (g.group === group.group ? { ...g, aliases } : g))
}

async function persist(next: KinshipNuanceGroup[]): Promise<boolean> {
  saving.value = true
  feedback.value = ''
  try {
    await store.save(next)
    return true
  } catch {
    feedback.value = t('settings.kinSaveError')
    return false
  } finally {
    saving.value = false
  }
}

async function add(group: KinshipNuanceGroup, row: Row): Promise<void> {
  const label = (drafts.value[row.key] ?? '').trim()
  if (!label) return
  if (!isFreeLabel(group, label)) {
    feedback.value = t('settings.kinAliasExists', { label })
    return
  }
  const alias: KinshipAlias = { id: null, base: row.base.id, label }
  if (await persist(withAliases(group, [...group.aliases, alias]))) {
    drafts.value[row.key] = ''
  }
}

async function requestDelete(group: KinshipNuanceGroup, alias: KinshipAlias): Promise<void> {
  if (!alias.id) return
  feedback.value = ''
  let count: number | null
  try {
    count = await store.usage(alias.id)
  } catch {
    count = null
  }
  if (count === 0) {
    await persist(withAliases(group, group.aliases.filter((a) => a.id !== alias.id)))
    return
  }
  pendingDelete.value = { group, alias, count }
}

async function confirmDelete(): Promise<void> {
  const pending = pendingDelete.value
  pendingDelete.value = null
  if (!pending) return
  await persist(withAliases(
    pending.group, pending.group.aliases.filter((a) => a.id !== pending.alias.id),
  ))
}

function deleteText(pending: PendingDelete): string {
  const base = pending.group.bases.find((b) => b.id === pending.alias.base)
  const label = pending.alias.label
  if (pending.count === null) return t('settings.kinAliasDeleteUnknown', { label })
  return t(
    'settings.kinAliasDeleteToBase',
    { label, base: base?.label ?? '', count: pending.count },
    pending.count,
  )
}
</script>

<template>
  <div class="sysdef">
    <h3 class="sysdef__heading">{{ t('settings.kinNuances') }}</h3>
    <p class="sysdef__desc">{{ t('settings.kinNuancesDesc') }}</p>

    <p v-if="loadFailed" class="sysdef__feedback">{{ t('settings.kinLoadError') }}</p>
    <p v-if="feedback" class="sysdef__feedback">{{ feedback }}</p>

    <section v-for="view in groupViews" :key="view.group.group" class="sysdef__group">
      <h4 class="sysdef__group-title">{{ view.title }}</h4>
      <div v-for="row in view.rows" :key="row.key" class="sysdef__row">
        <span class="sysdef__base">{{ row.base.label }}</span>
        <div class="sysdef__aliases">
          <span v-for="alias in row.aliases" :key="alias.id ?? alias.label" class="sysdef__chip">
            {{ alias.label }}
            <button
              type="button"
              class="sysdef__chip-remove"
              :disabled="saving"
              :title="t('settings.kinAliasDelete')"
              :aria-label="t('settings.kinAliasDelete')"
              @click="requestDelete(view.group, alias)"
            >
              <Icon icon="mdi:close" width="12" height="12" />
            </button>
          </span>
          <input
            v-model="drafts[row.key]"
            class="sysdef__input"
            :placeholder="t('settings.kinAliasPlaceholder')"
            :title="t('settings.kinAliasPlaceholder')"
            :disabled="saving"
            @keydown.enter.prevent="add(view.group, row)"
          />
          <button
            type="button"
            class="sysdef__add"
            :disabled="saving || !(drafts[row.key] ?? '').trim()"
            :title="t('settings.kinAliasAdd')"
            :aria-label="t('settings.kinAliasAdd')"
            @click="add(view.group, row)"
          >
            <Icon icon="mdi:plus" width="14" height="14" />
          </button>
        </div>
      </div>
      <div
        v-if="pendingDelete && pendingDelete.group.group === view.group.group"
        class="sysdef__confirm"
      >
        <span>{{ deleteText(pendingDelete) }}</span>
        <button type="button" class="sysdef__btn" @click="pendingDelete = null">
          {{ t('actions.cancel') }}
        </button>
        <button type="button" class="sysdef__btn sysdef__btn--danger" @click="confirmDelete">
          {{ t('actions.delete') }}
        </button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.sysdef {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.sysdef__heading {
  margin: 0;
  font-size: 0.9375rem;
  font-weight: 600;
  color: var(--color-text);
}

.sysdef__desc {
  margin: 0;
  font-size: 0.8125rem;
  color: var(--color-text-muted);
  line-height: 1.5;
}

.sysdef__feedback {
  margin: 0;
  font-size: 0.8125rem;
  color: #e05353;
}

.sysdef__group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.sysdef__group-title {
  margin: 0;
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--color-text);
}

.sysdef__row {
  display: grid;
  grid-template-columns: minmax(90px, 140px) 1fr;
  align-items: center;
  gap: 10px;
  padding: 6px 8px;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-bg);
}

.sysdef__base {
  font-size: 0.8125rem;
  color: var(--color-text);
}

.sysdef__aliases {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.sysdef__chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 4px 2px 8px;
  border-radius: 10px;
  background: var(--color-hover);
  font-size: 0.75rem;
  color: var(--color-text);
}

.sysdef__chip-remove {
  display: flex;
  align-items: center;
  padding: 1px;
  border: none;
  border-radius: 50%;
  background: none;
  color: var(--color-text-muted);
  cursor: pointer;
}

.sysdef__chip-remove:hover:not(:disabled) {
  color: #e05353;
}

.sysdef__input {
  flex: 1;
  min-width: 200px;
  padding: 4px 8px;
  font-size: 0.75rem;
  font-family: inherit;
  border: 1px solid var(--color-border);
  border-radius: 5px;
  background: var(--color-bg);
  color: var(--color-text);
  text-overflow: ellipsis;
}

.sysdef__add {
  display: flex;
  align-items: center;
  padding: 4px;
  border: 1px solid var(--color-border);
  border-radius: 5px;
  background: none;
  color: var(--color-text);
  cursor: pointer;
}

.sysdef__add:disabled,
.sysdef__chip-remove:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.sysdef__confirm {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  font-size: 0.8125rem;
  color: var(--color-text);
}

.sysdef__btn {
  padding: 4px 10px;
  font-size: 0.75rem;
  font-family: inherit;
  border: 1px solid var(--color-border);
  border-radius: 5px;
  background: none;
  color: var(--color-text);
  cursor: pointer;
}

.sysdef__btn--danger {
  border-color: #e05353;
  color: #e05353;
}
</style>

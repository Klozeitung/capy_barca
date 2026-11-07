<script setup lang="ts">
/**
 * FamilyTreeSettingsSection
 *
 * The settings of a family tree view, shown in the view settings modal:
 *
 * - Root: the entry the tree is drawn around, found by title.
 * - Instant: the point in time the tree shows, or the overall view.
 * - Scope: who belongs to the tree (``FAMILY_TREE_SCOPES``).
 * - Properties shown under each life span.
 * - Overlays: two-way relations within the database, outside the family
 *   set, drawn on top in a colour, with arrows as chosen, optionally with
 *   people outside the tree and with their nuances. An overlay whose
 *   property was deleted stays, marked invalid, until it is removed here.
 * - "Determine family tree": runs the sibling mapper once the root is set,
 *   and lists what it wrote and every contradiction it found, with links to
 *   the entries concerned.
 *
 * Every change is emitted at once as a partial view (``update``); the modal
 * hands it on and the parent saves it. Without write access everything is
 * shown read-only and the mapper is not offered.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import {
  FAMILY_TREE_SCOPES,
  SELECT_OPTION_COLORS,
  useDatabaseStore,
  type DatabaseEntry,
  type DatabaseView,
  type FamilyTreeOverlay,
  type FamilyTreeScope,
  type PropertySchema,
} from '@/stores/database'
import { kinshipRole } from '@/stores/propertyTypes'
import { runFamilyTreeMapper, type MapperResult } from '@/composables/useFamilyTree'
import { overlayColor } from '../familyTreeLayout'

const props = defineProps<{
  view: DatabaseView
  schemas: PropertySchema[]
  databaseId: string
  editable: boolean
}>()

const emit = defineEmits<{
  (e: 'update', patch: Partial<DatabaseView>): void
  (e: 'close'): void
}>()

const { t } = useI18n()
const dbStore = useDatabaseStore()

// ── Root ──────────────────────────────────────────────────────────────────────

const rootQuery = ref('')
const rootResults = ref<DatabaseEntry[]>([])
let searchTimer: ReturnType<typeof setTimeout> | null = null

const rootTitle = computed(() => {
  const id = props.view.familyTreeRootId
  if (!id) return ''
  const resolved = dbStore.relationTitles[id]
  return resolved ? resolved.title || t('main.untitled') : ''
})

onMounted(() => {
  if (props.view.familyTreeRootId) {
    dbStore.resolveEntryTitles(props.databaseId, [props.view.familyTreeRootId])
  }
})

watch(rootQuery, (query) => {
  if (searchTimer) clearTimeout(searchTimer)
  if (!query.trim()) {
    rootResults.value = []
    return
  }
  searchTimer = setTimeout(async () => {
    try {
      rootResults.value = (await dbStore.searchEntries(props.databaseId, query, 8)).entries
    } catch {
      rootResults.value = []
    }
  }, 250)
})

function chooseRoot(entry: DatabaseEntry): void {
  dbStore.relationTitles[entry.id] = {
    id: entry.id,
    title: (entry.content?.title as string | undefined) ?? null,
    database_id: props.databaseId,
    icon: entry.icon,
  }
  rootQuery.value = ''
  rootResults.value = []
  mapperResult.value = null
  emit('update', { familyTreeRootId: entry.id })
}

// ── Instant ───────────────────────────────────────────────────────────────────

const overall = computed(() => !props.view.familyTreeAt)
const atDate = computed(() => (props.view.familyTreeAt ?? '').slice(0, 10))
const atTime = computed(() => (props.view.familyTreeAt ?? '').slice(11, 16))

function setInstant(date: string, time: string): void {
  if (!date) {
    emit('update', { familyTreeAt: undefined })
    return
  }
  emit('update', { familyTreeAt: time ? `${date}T${time}` : date })
}

function setOverall(value: boolean): void {
  if (value) emit('update', { familyTreeAt: undefined })
  else setInstant(new Date().toISOString().slice(0, 10), '')
}

// ── Scope ─────────────────────────────────────────────────────────────────────

const scope = computed<FamilyTreeScope>(() => props.view.familyTreeScope ?? 'lineage')

// ── Properties under the life span ────────────────────────────────────────────

const lineCandidates = computed(() => props.schemas.filter((s) => s.type !== 'alerts'))
const lineSelection = computed(() => new Set(props.view.familyTreeProperties ?? []))

function toggleLineProperty(schemaId: string): void {
  const selected = new Set(lineSelection.value)
  if (selected.has(schemaId)) selected.delete(schemaId)
  else selected.add(schemaId)
  emit('update', {
    familyTreeProperties: lineCandidates.value.map((s) => s.id).filter((id) => selected.has(id)),
  })
}

// ── Overlays ──────────────────────────────────────────────────────────────────

/** Two-way relations within the database that are not part of the family set. */
const overlayCandidates = computed(() =>
  props.schemas.filter((s) => {
    const config = s.config ?? {}
    return (
      s.type === 'relation'
      && String(config.target_database_id ?? '') === props.databaseId
      && (config.direction === 'bilateral' || config.direction === 'bilateral_self')
      && kinshipRole(s) === null
    )
  }),
)

const overlays = computed<FamilyTreeOverlay[]>(() => props.view.familyTreeOverlays ?? [])
const addable = computed(() =>
  overlayCandidates.value.filter((s) => !overlays.value.some((o) => o.schemaId === s.id)),
)
const newOverlay = ref('')

function schemaOf(overlay: FamilyTreeOverlay): PropertySchema | undefined {
  return overlayCandidates.value.find((s) => s.id === overlay.schemaId)
}

function isSymmetric(overlay: FamilyTreeOverlay): boolean {
  return schemaOf(overlay)?.config?.direction === 'bilateral_self'
}

function saveOverlays(next: FamilyTreeOverlay[]): void {
  emit('update', { familyTreeOverlays: next })
}

function addOverlay(): void {
  const schema = overlayCandidates.value.find((s) => s.id === newOverlay.value)
  newOverlay.value = ''
  if (!schema) return
  const symmetric = schema.config?.direction === 'bilateral_self'
  saveOverlays([
    ...overlays.value,
    { schemaId: schema.id, direction: symmetric ? 'both' : 'forward', color: 'default', showOutsiders: false, showNuances: false },
  ])
}

function changeOverlay(index: number, patch: Partial<FamilyTreeOverlay>): void {
  saveOverlays(overlays.value.map((o, i) => (i === index ? { ...o, ...patch } : o)))
}

function removeOverlay(index: number): void {
  saveOverlays(overlays.value.filter((_, i) => i !== index))
}

// ── Mapper ────────────────────────────────────────────────────────────────────

const mapping = ref(false)
const mapperFailed = ref(false)
const mapperResult = ref<MapperResult | null>(null)

async function runMapper(): Promise<void> {
  if (!props.editable || !props.view.familyTreeRootId || mapping.value) return
  mapping.value = true
  mapperFailed.value = false
  try {
    mapperResult.value = await runFamilyTreeMapper(props.databaseId, props.view)
  } catch {
    mapperFailed.value = true
    mapperResult.value = null
  } finally {
    mapping.value = false
  }
}

function entryTitle(id: string): string {
  return mapperResult.value?.entries[id]?.title || t('main.untitled')
}

/** The entries a finding names, with their titles. */
function findingEntries(entries: string[]): { id: string; title: string }[] {
  return entries.map((id) => ({ id, title: entryTitle(id) }))
}
</script>

<template>
  <div class="fts">
    <!-- Root -->
    <div class="fts__field">
      <span class="fts__label">{{ t('db.familyTree.root') }}</span>
      <div class="fts__root">
        <span v-if="view.familyTreeRootId" class="fts__root-current">
          <Icon icon="mdi:account-circle-outline" width="14" height="14" />
          {{ rootTitle || t('db.familyTree.rootUnresolved') }}
        </span>
        <span v-else class="fts__muted">{{ t('db.familyTree.noRootShort') }}</span>
      </div>
      <div v-if="editable" class="fts__search">
        <input
          v-model="rootQuery"
          class="fts__input"
          :placeholder="t('db.familyTree.rootSearch')"
        />
        <ul v-if="rootResults.length" class="fts__results">
          <li v-for="entry in rootResults" :key="entry.id">
            <button type="button" class="fts__result" @click="chooseRoot(entry)">
              {{ (entry.content?.title as string | undefined) || t('main.untitled') }}
            </button>
          </li>
        </ul>
      </div>
    </div>

    <!-- Instant -->
    <div class="fts__field">
      <span class="fts__label">{{ t('db.familyTree.instant') }}</span>
      <label class="fts__check">
        <input type="checkbox" :checked="overall" :disabled="!editable" @change="setOverall(($event.target as HTMLInputElement).checked)" />
        {{ t('db.familyTree.overall') }}
      </label>
      <div v-if="!overall" class="fts__row">
        <input
          type="date"
          class="fts__input fts__input--date"
          :value="atDate"
          :disabled="!editable"
          @change="setInstant(($event.target as HTMLInputElement).value, atTime)"
        />
        <input
          type="time"
          class="fts__input fts__input--time"
          :value="atTime"
          :disabled="!editable"
          @change="setInstant(atDate, ($event.target as HTMLInputElement).value)"
        />
      </div>
    </div>

    <!-- Scope -->
    <div class="fts__field">
      <span class="fts__label">{{ t('db.familyTree.scope') }}</span>
      <select
        class="fts__select"
        :value="scope"
        :disabled="!editable"
        @change="emit('update', { familyTreeScope: ($event.target as HTMLSelectElement).value as FamilyTreeScope })"
      >
        <option v-for="s in FAMILY_TREE_SCOPES" :key="s" :value="s">{{ t(`db.familyTree.scopes.${s}`) }}</option>
      </select>
      <span class="fts__hint">{{ t(`db.familyTree.scopeHints.${scope}`) }}</span>
    </div>

    <!-- Properties under the life span -->
    <div class="fts__field">
      <span class="fts__label">{{ t('db.familyTree.properties') }}</span>
      <span class="fts__hint">{{ t('db.familyTree.propertiesHint') }}</span>
      <div class="fts__checks">
        <label v-for="schema in lineCandidates" :key="schema.id" class="fts__check">
          <input
            type="checkbox"
            :checked="lineSelection.has(schema.id)"
            :disabled="!editable"
            @change="toggleLineProperty(schema.id)"
          />
          {{ schema.name }}
        </label>
      </div>
    </div>

    <!-- Overlays -->
    <div class="fts__field">
      <span class="fts__label">{{ t('db.familyTree.overlays') }}</span>
      <span class="fts__hint">{{ t('db.familyTree.overlaysHint') }}</span>
      <div v-for="(overlay, index) in overlays" :key="`${overlay.schemaId}-${index}`" class="fts__overlay">
        <div class="fts__overlay-head">
          <span
            class="fts__overlay-name"
            :class="{ 'fts__overlay-name--invalid': !schemaOf(overlay) }"
          >
            {{ schemaOf(overlay)?.name ?? t('db.familyTree.overlayInvalid') }}
          </span>
          <button
            v-if="editable"
            type="button"
            class="fts__icon-btn"
            :title="t('db.familyTree.overlayRemove')"
            :aria-label="t('db.familyTree.overlayRemove')"
            @click="removeOverlay(index)"
          >
            <Icon icon="mdi:close" width="13" height="13" />
          </button>
        </div>
        <template v-if="schemaOf(overlay)">
          <div class="fts__row">
            <select
              class="fts__select"
              :value="overlay.direction"
              :disabled="!editable"
              @change="changeOverlay(index, { direction: ($event.target as HTMLSelectElement).value as FamilyTreeOverlay['direction'] })"
            >
              <option v-if="!isSymmetric(overlay)" value="forward">{{ t('db.familyTree.directionForward') }}</option>
              <option value="both">{{ t('db.familyTree.directionBoth') }}</option>
              <option value="none">{{ t('db.familyTree.directionNone') }}</option>
            </select>
            <div class="fts__colors">
              <button
                v-for="c in SELECT_OPTION_COLORS"
                :key="c.key"
                type="button"
                class="fts__color"
                :class="{ 'fts__color--active': (overlay.color ?? 'default') === c.key }"
                :style="{ background: overlayColor(c.key) }"
                :title="c.label"
                :disabled="!editable"
                @click="changeOverlay(index, { color: c.key })"
              />
            </div>
          </div>
          <label class="fts__check">
            <input
              type="checkbox"
              :checked="overlay.showOutsiders"
              :disabled="!editable"
              @change="changeOverlay(index, { showOutsiders: ($event.target as HTMLInputElement).checked })"
            />
            {{ t('db.familyTree.showOutsiders') }}
          </label>
          <label class="fts__check">
            <input
              type="checkbox"
              :checked="overlay.showNuances"
              :disabled="!editable"
              @change="changeOverlay(index, { showNuances: ($event.target as HTMLInputElement).checked })"
            />
            {{ t('db.familyTree.showNuances') }}
          </label>
        </template>
      </div>
      <div v-if="editable && addable.length" class="fts__row">
        <select v-model="newOverlay" class="fts__select">
          <option value="" disabled>{{ t('db.familyTree.overlayChoose') }}</option>
          <option v-for="schema in addable" :key="schema.id" :value="schema.id">{{ schema.name }}</option>
        </select>
        <button type="button" class="fts__btn" :disabled="!newOverlay" @click="addOverlay">
          <Icon icon="mdi:plus" width="13" height="13" />
          {{ t('db.familyTree.overlayAdd') }}
        </button>
      </div>
      <span v-else-if="!overlayCandidates.length" class="fts__hint">{{ t('db.familyTree.overlayNone') }}</span>
    </div>

    <!-- Mapper -->
    <div v-if="editable" class="fts__field fts__field--mapper">
      <span class="fts__label">{{ t('db.familyTree.mapper') }}</span>
      <span class="fts__hint">{{ t('db.familyTree.mapperHint') }}</span>
      <button
        type="button"
        class="fts__btn fts__btn--primary"
        :disabled="!view.familyTreeRootId || mapping"
        :title="!view.familyTreeRootId ? t('db.familyTree.mapperNeedsRoot') : undefined"
        @click="runMapper"
      >
        <Icon :icon="mapping ? 'mdi:loading' : 'mdi:family-tree'" width="14" height="14" :class="{ 'fts__spin': mapping }" />
        {{ t('db.familyTree.mapperRun') }}
      </button>
      <p v-if="mapperFailed" class="fts__error">{{ t('db.familyTree.mapperError') }}</p>
      <div v-if="mapperResult" class="fts__result-box">
        <p class="fts__summary">
          {{ t('db.familyTree.mapperSummary', {
            added: mapperResult.added,
            updated: mapperResult.updated,
            removed: mapperResult.removed,
            filled: mapperResult.filled,
          }) }}
        </p>
        <p v-if="!mapperResult.conflicts.length" class="fts__muted">{{ t('db.familyTree.mapperNoConflicts') }}</p>
        <template v-else>
          <p class="fts__subtitle">{{ t('db.familyTree.mapperConflicts', { count: mapperResult.conflicts.length }, mapperResult.conflicts.length) }}</p>
          <ul class="fts__findings">
            <li v-for="(finding, i) in mapperResult.conflicts" :key="`c${i}`" class="fts__finding">
              <span>{{ t(`db.familyTree.conflict.${finding.kind}`) }}</span>
              <span class="fts__finding-entries">
                <RouterLink
                  v-for="entry in findingEntries(finding.entries)"
                  :key="entry.id"
                  :to="`/blocks/${entry.id}`"
                  class="fts__link"
                  @click="emit('close')"
                >{{ entry.title }}</RouterLink>
              </span>
              <span class="fts__muted">{{ t(`db.familyTree.resolve.${finding.kind}`) }}</span>
            </li>
          </ul>
        </template>
        <template v-if="mapperResult.warnings.length">
          <p class="fts__subtitle">{{ t('db.familyTree.mapperWarnings', { count: mapperResult.warnings.length }, mapperResult.warnings.length) }}</p>
          <ul class="fts__findings">
            <li v-for="(finding, i) in mapperResult.warnings" :key="`w${i}`" class="fts__finding">
              <span>{{ t(`db.familyTree.conflict.${finding.kind}`) }}</span>
              <span class="fts__finding-entries">
                <RouterLink
                  v-for="entry in findingEntries(finding.entries)"
                  :key="entry.id"
                  :to="`/blocks/${entry.id}`"
                  class="fts__link"
                  @click="emit('close')"
                >{{ entry.title }}</RouterLink>
              </span>
            </li>
          </ul>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.fts {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.fts__field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.fts__label,
.fts__subtitle {
  margin: 0;
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--color-text);
}

.fts__hint,
.fts__muted {
  margin: 0;
  font-size: 0.75rem;
  color: var(--color-text-muted);
  line-height: 1.4;
}

.fts__error {
  margin: 0;
  font-size: 0.75rem;
  color: #e05353;
}

.fts__row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.fts__root-current {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 0.8125rem;
  color: var(--color-text);
}

.fts__search {
  position: relative;
}

.fts__input,
.fts__select {
  padding: 5px 8px;
  font-size: 0.8125rem;
  font-family: inherit;
  border: 1px solid var(--color-border);
  border-radius: 5px;
  background: var(--color-bg);
  color: var(--color-text);
}

.fts__input {
  width: 100%;
  box-sizing: border-box;
}

.fts__input--date,
.fts__input--time {
  width: auto;
}

.fts__results {
  position: absolute;
  z-index: 5;
  left: 0;
  right: 0;
  margin: 2px 0 0;
  padding: 4px;
  list-style: none;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-surface);
  max-height: 200px;
  overflow-y: auto;
}

.fts__result {
  display: block;
  width: 100%;
  padding: 4px 8px;
  border: none;
  border-radius: 4px;
  background: none;
  color: var(--color-text);
  font-size: 0.8125rem;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
}

.fts__result:hover {
  background: var(--color-hover);
}

.fts__check {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 0.8125rem;
  color: var(--color-text);
}

.fts__checks {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 4px 12px;
}

.fts__overlay {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px;
  border: 1px solid var(--color-border);
  border-radius: 6px;
}

.fts__overlay-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.fts__overlay-name {
  font-size: 0.8125rem;
  color: var(--color-text);
}

.fts__overlay-name--invalid {
  color: #e05353;
}

.fts__colors {
  display: flex;
  gap: 4px;
}

.fts__color {
  width: 16px;
  height: 16px;
  padding: 0;
  border: 2px solid transparent;
  border-radius: 50%;
  cursor: pointer;
}

.fts__color--active {
  border-color: var(--color-text);
}

.fts__icon-btn {
  display: flex;
  padding: 2px;
  border: none;
  background: none;
  color: var(--color-text-muted);
  cursor: pointer;
}

.fts__btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  align-self: flex-start;
  padding: 5px 10px;
  font-size: 0.8125rem;
  font-family: inherit;
  border: 1px solid var(--color-border);
  border-radius: 5px;
  background: none;
  color: var(--color-text);
  cursor: pointer;
}

.fts__btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.fts__btn--primary {
  border-color: var(--color-accent);
  color: var(--color-accent);
}

.fts__spin {
  animation: fts-spin 1s linear infinite;
}

@keyframes fts-spin {
  to { transform: rotate(360deg); }
}

.fts__result-box {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px;
  border-radius: 6px;
  background: var(--color-surface);
}

.fts__summary {
  margin: 0;
  font-size: 0.8125rem;
  color: var(--color-text);
}

.fts__findings {
  margin: 0;
  padding-left: 18px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 0.8125rem;
  color: var(--color-text);
}

.fts__finding {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.fts__finding-entries {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
}

.fts__link {
  color: var(--color-accent);
  text-decoration: underline;
}
</style>

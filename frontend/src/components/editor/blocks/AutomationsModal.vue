<script setup lang="ts">
/**
 * AutomationsModal
 *
 * Two-pane modal for managing database automations.
 *
 * List pane
 * ---------
 * Shows all automations for the current database.  Each row has a toggle
 * (enable/disable), a name, a one-line trigger summary, an edit button,
 * and a two-step delete button.
 *
 * Form pane — Trigger section
 * ---------------------------
 * Array of trigger cards (OR semantics: automation fires when any trigger matches).
 * Each card: Type → Database → Property → Actor filter.
 * An "+ Add trigger" button is always visible at the bottom of the section.
 *
 * Form pane — Action section
 * --------------------------
 * Ordered list of EditProperty action cards:
 *   Level 1: Action type
 *   Level 2: Target database
 *   Level 3: Filter (all entries / where <conditions>)
 *   Level 4a: Property to set
 *   Level 4b: New value (type-aware)
 *
 * Data format
 * -----------
 * Triggers: always saved as an array.  Engine applies OR logic.
 * Actions: bulk endpoint  PUT /api/databases/<db>/bulk-values/<schema>
 *          with  filter: { mode: "all"|"where"|"triggered", groups: [...] }
 *
 * Backward compatibility
 * ----------------------
 * Legacy single-trigger dicts and old triggered-entry endpoints are both
 * handled during load; they are converted to the new format on save.
 */
import { ref, computed, onMounted } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import {
  useAutomationsStore,
  getTriggers,
  type AutomationTrigger,
  type AutomationAction,
} from '@/stores/automations'
import { useDatabaseStore, type PropertySchema, type FilterOperator } from '@/stores/database'
import { getOperatorsForSchemaId, filterNeedsValue } from '@/composables/useFilterPanel'
import { apiClient } from '@/api/client'

// ── Props / emits ─────────────────────────────────────────────────────────────

const props = defineProps<{
  databaseId: string
  schemas:    PropertySchema[]
}>()

const emit = defineEmits<{ (e: 'close'): void }>()

// ── Dependencies ──────────────────────────────────────────────────────────────

const { t } = useI18n()
const store   = useAutomationsStore()
const dbStore = useDatabaseStore()

// ── Data ──────────────────────────────────────────────────────────────────────

const automations = computed(() => store.getForDatabase(props.databaseId))
const loading     = ref(true)
const error       = ref<string | null>(null)
const saving      = ref(false)

// ── Users ─────────────────────────────────────────────────────────────────────

interface UserEntry {
  id:       string
  username: string
}

const allUsers    = ref<UserEntry[]>([])
const usersLoaded = ref(false)

async function ensureUsersLoaded(): Promise<void> {
  if (usersLoaded.value) return
  try {
    allUsers.value = await apiClient.get<UserEntry[]>('/api/users/names')
    usersLoaded.value = true
  } catch {
    // Non-critical; actor list will be empty.
  }
}

// ── Schema helpers ────────────────────────────────────────────────────────────

const TRIGGER_EXCLUDED = new Set([
  'id', 'created_by', 'created_time', 'last_edited_by', 'last_edited_time',
  'formula', 'rollup', 'sub_item',
])

const ACTIONABLE_TYPES = new Set([
  'text', 'number', 'checkbox', 'select', 'email', 'phone', 'url',
])

async function ensureDbSchemas(dbId: string): Promise<void> {
  if (!dbId) return
  if (dbStore.getSchemas(dbId).length === 0) {
    await dbStore.fetchSchemas(dbId)
  }
}

// ── List state ────────────────────────────────────────────────────────────────

const confirmDeleteId = ref<string | null>(null)

function requestDelete(id: string): void {
  if (confirmDeleteId.value === id) {
    doDelete(id)
  } else {
    confirmDeleteId.value = id
    setTimeout(() => {
      if (confirmDeleteId.value === id) confirmDeleteId.value = null
    }, 3000)
  }
}

async function doDelete(id: string): Promise<void> {
  try {
    await store.remove(id, props.databaseId)
    confirmDeleteId.value = null
  } catch {
    error.value = t('automations.errors.deleteFailed')
  }
}

async function onToggle(id: string): Promise<void> {
  try {
    await store.toggle(id, props.databaseId)
  } catch {
    error.value = t('automations.errors.saveFailed')
  }
}

// ── Form state — top-level ────────────────────────────────────────────────────

const editingId = ref<string | null>(null)
const formError  = ref<string | null>(null)
const formName   = ref('')

// ── Trigger form state ────────────────────────────────────────────────────────

interface FormActorEntry {
  uuid:     string
  username: string
  state:    'unselected' | 'positive' | 'negative'
}

interface FormTriggerItem {
  _id:                    string
  type:                   'PropertyChanged'
  dbId:                   string
  propertyId:             string
  actorMode:              'any' | 'specific'
  actorIncludeAutomation: boolean
  actorEntries:           FormActorEntry[]
}

const formTriggers = ref<FormTriggerItem[]>([])

function newTriggerItem(): FormTriggerItem {
  return {
    _id:                    crypto.randomUUID(),
    type:                   'PropertyChanged',
    dbId:                   props.databaseId,
    propertyId:             '',
    actorMode:              'any',
    actorIncludeAutomation: false,
    actorEntries:           allUsers.value.map(u => ({
      uuid: u.id, username: u.username, state: 'unselected' as const,
    })),
  }
}

function addTrigger(): void {
  formTriggers.value.push(newTriggerItem())
}

function removeTrigger(idx: number): void {
  if (formTriggers.value.length <= 1) return
  formTriggers.value.splice(idx, 1)
}

function triggerDbSchemas(dbId: string): PropertySchema[] {
  return dbStore.getSchemas(dbId).filter(s => !TRIGGER_EXCLUDED.has(s.type))
}

async function onTriggerDbChange(triggerIdx: number, newDbId: string): Promise<void> {
  formTriggers.value[triggerIdx].dbId       = newDbId
  formTriggers.value[triggerIdx].propertyId = ''
  await ensureDbSchemas(newDbId)
}

function cycleActorState(entry: FormActorEntry): void {
  const cycle: FormActorEntry['state'][] = ['unselected', 'positive', 'negative']
  const idx = cycle.indexOf(entry.state)
  entry.state = cycle[(idx + 1) % cycle.length]
}

// ── Action form state ─────────────────────────────────────────────────────────

interface ActionFilterCondition {
  _id:      string
  schemaId: string
  operator: FilterOperator
  value:    string
}

interface ActionFilterGroup {
  _id:         string
  conjunction: 'and' | 'or'
  conditions:  ActionFilterCondition[]
}

interface FormActionFilter {
  mode:   'all' | 'where' | 'triggered'
  groups: ActionFilterGroup[]
}

interface FormActionItem {
  _id:              string
  type:             'EditProperty'
  targetDbId:       string
  filter:           FormActionFilter
  targetPropertyId: string
  valueStr:         string
  valueBool:        boolean
}

const formActions = ref<FormActionItem[]>([])

function newFilterGroup(dbId: string): ActionFilterGroup {
  const firstSchema = dbStore.getSchemas(dbId).find(s => !TRIGGER_EXCLUDED.has(s.type))
  const schemaId    = firstSchema?.id ?? ''
  const schemas     = dbStore.getSchemas(dbId)
  const operator    = schemaId
    ? (getOperatorsForSchemaId(schemaId, schemas, [], '__name__')[0] ?? 'eq') as FilterOperator
    : 'eq' as FilterOperator
  return {
    _id:         crypto.randomUUID(),
    conjunction: 'and',
    conditions:  [{ _id: crypto.randomUUID(), schemaId, operator, value: '' }],
  }
}

function newActionItem(): FormActionItem {
  return {
    _id:              crypto.randomUUID(),
    type:             'EditProperty',
    targetDbId:       props.databaseId,
    filter:           { mode: 'triggered', groups: [] },
    targetPropertyId: '',
    valueStr:         '',
    valueBool:        false,
  }
}

function addAction(): void {
  formActions.value.push(newActionItem())
  ensureDbSchemas(props.databaseId)
}

function removeAction(index: number): void {
  formActions.value.splice(index, 1)
}

function moveActionUp(index: number): void {
  if (index === 0) return
  const arr = [...formActions.value]
  ;[arr[index - 1], arr[index]] = [arr[index], arr[index - 1]]
  formActions.value = arr
}

function moveActionDown(index: number): void {
  if (index >= formActions.value.length - 1) return
  const arr = [...formActions.value]
  ;[arr[index], arr[index + 1]] = [arr[index + 1], arr[index]]
  formActions.value = arr
}

async function onActionDbChange(index: number, newDbId: string): Promise<void> {
  const action = formActions.value[index]
  action.targetDbId       = newDbId
  action.targetPropertyId = ''
  action.valueStr         = ''
  action.valueBool        = false
  await ensureDbSchemas(newDbId)
  if (action.filter.mode === 'where') {
    action.filter.groups = [newFilterGroup(newDbId)]
  }
}

function onActionPropertyChange(index: number, newSchemaId: string): void {
  formActions.value[index].targetPropertyId = newSchemaId
  formActions.value[index].valueStr         = ''
  formActions.value[index].valueBool        = false
}

function setActionFilterMode(actionIdx: number, mode: 'all' | 'where' | 'triggered'): void {
  const action = formActions.value[actionIdx]
  action.filter.mode = mode
  if (mode === 'where' && action.filter.groups.length === 0) {
    action.filter.groups = [newFilterGroup(action.targetDbId)]
  }
}

// ── Action filter — condition management ──────────────────────────────────────

function actionFilterSchemas(dbId: string): PropertySchema[] {
  return dbStore.getSchemas(dbId).filter(s => s.type !== 'sub_item')
}

function filterCondSchema(
  action: FormActionItem,
  groupIdx: number,
  condIdx:  number,
): PropertySchema | undefined {
  const cond = action.filter.groups[groupIdx]?.conditions[condIdx]
  if (!cond?.schemaId) return undefined
  return dbStore.getSchemas(action.targetDbId).find(s => s.id === cond.schemaId)
}

function filterCondOperators(
  actionIdx: number,
  groupIdx:  number,
  condIdx:   number,
): FilterOperator[] {
  const action = formActions.value[actionIdx]
  const cond   = action.filter.groups[groupIdx]?.conditions[condIdx]
  if (!cond) return ['eq']
  return getOperatorsForSchemaId(
    cond.schemaId, dbStore.getSchemas(action.targetDbId), [], '__name__',
  ) as FilterOperator[]
}

function filterCondSelectOptions(
  action:   FormActionItem,
  groupIdx: number,
  condIdx:  number,
): { value: string; label: string }[] {
  const schema = filterCondSchema(action, groupIdx, condIdx)
  const opts   = schema?.config?.options
  if (!Array.isArray(opts)) return []
  return opts.map((o: { value?: string; label?: string }) => ({
    value: o.value ?? '',
    label: o.value ?? '',
  }))
}

function onFilterCondSchemaChange(
  actionIdx: number,
  groupIdx:  number,
  condIdx:   number,
  event:     Event,
): void {
  const newSchemaId = (event.target as HTMLSelectElement).value
  const action = formActions.value[actionIdx]
  const cond   = action.filter.groups[groupIdx]?.conditions[condIdx]
  if (!cond) return
  cond.schemaId = newSchemaId
  const ops = getOperatorsForSchemaId(
    newSchemaId, dbStore.getSchemas(action.targetDbId), [], '__name__',
  )
  cond.operator = (ops[0] ?? 'eq') as FilterOperator
  cond.value    = ''
}

function onFilterCondOperatorChange(
  actionIdx: number,
  groupIdx:  number,
  condIdx:   number,
  event:     Event,
): void {
  const newOp = (event.target as HTMLSelectElement).value as FilterOperator
  const cond  = formActions.value[actionIdx]?.filter.groups[groupIdx]?.conditions[condIdx]
  if (!cond) return
  cond.operator = newOp
  if (!filterNeedsValue(newOp)) cond.value = ''
}

function addFilterCondition(actionIdx: number, groupIdx: number): void {
  const action = formActions.value[actionIdx]
  const group  = action.filter.groups[groupIdx]
  if (!group) return
  const schemas  = dbStore.getSchemas(action.targetDbId)
  const first    = actionFilterSchemas(action.targetDbId)[0]
  const schemaId = first?.id ?? ''
  const operator = schemaId
    ? (getOperatorsForSchemaId(schemaId, schemas, [], '__name__')[0] ?? 'eq') as FilterOperator
    : 'eq' as FilterOperator
  group.conditions.push({ _id: crypto.randomUUID(), schemaId, operator, value: '' })
}

function removeFilterCondition(actionIdx: number, groupIdx: number, condIdx: number): void {
  const action = formActions.value[actionIdx]
  const group  = action.filter.groups[groupIdx]
  if (!group) return
  group.conditions.splice(condIdx, 1)
  if (group.conditions.length === 0) {
    action.filter.groups.splice(groupIdx, 1)
    if (action.filter.groups.length === 0) action.filter.mode = 'triggered'
  }
}

// ── Action target helpers ─────────────────────────────────────────────────────

function actionTargetSchemas(dbId: string): PropertySchema[] {
  return dbStore.getSchemas(dbId).filter(s => ACTIONABLE_TYPES.has(s.type))
}

function actionTargetSchema(action: FormActionItem): PropertySchema | undefined {
  return dbStore.getSchemas(action.targetDbId).find(s => s.id === action.targetPropertyId)
}

function actionSelectOptions(action: FormActionItem): { value: string; label: string }[] {
  const schema = actionTargetSchema(action)
  const opts = schema?.config?.options
  if (!Array.isArray(opts)) return []
  return opts.map((o: { value?: string; label?: string }) => ({
    value: o.value ?? '',
    label: o.value ?? '',
  }))
}

// ── Trigger summary (list view) ───────────────────────────────────────────────

function _singleTriggerSummary(trigger: AutomationTrigger): string {
  if (!trigger.property_uuid) return t('automations.trigger.anyProperty')
  const schemas = [
    ...dbStore.getSchemas(trigger.db_uuid || props.databaseId),
    ...props.schemas,
  ]
  const name = schemas.find(s => s.id === trigger.property_uuid)?.name ?? trigger.property_uuid
  return t('automations.trigger.specificProperty', { name })
}

function triggerSummary(auto: { trigger: AutomationTrigger | AutomationTrigger[] }): string {
  const triggers = getTriggers(auto as any)
  if (triggers.length === 1) return _singleTriggerSummary(triggers[0])
  return t('automations.trigger.multipleSummary', { n: triggers.length })
}

// ── Value serialisation ───────────────────────────────────────────────────────

function buildActionBody(
  schema: PropertySchema,
  strVal: string,
  boolVal: boolean,
): Record<string, unknown> {
  switch (schema.type) {
    case 'select':  return { value: strVal ? { option: strVal } : null }
    case 'checkbox':return { value: { checked: boolVal } }
    case 'number':  return { value: strVal !== '' ? { number: Number(strVal) } : null }
    default:        return { value: strVal !== '' ? { text: strVal } : null }
  }
}

function parseActionValue(
  schema: PropertySchema,
  body: Record<string, unknown>,
): { strVal: string; boolVal: boolean } {
  const v = (body?.value ?? {}) as Record<string, unknown>
  switch (schema.type) {
    case 'select':  return { strVal: (v.option as string) ?? '', boolVal: false }
    case 'checkbox':return { strVal: '', boolVal: (v.checked as boolean) ?? false }
    case 'number':  return { strVal: v.number !== undefined ? String(v.number) : '', boolVal: false }
    default:        return { strVal: (v.text as string) ?? '', boolVal: false }
  }
}

// ── Form open / close ─────────────────────────────────────────────────────────

async function openCreate(): Promise<void> {
  editingId.value = ''
  formName.value  = ''
  formError.value = null

  await Promise.all([
    dbStore.fetchAllDatabases(),
    ensureDbSchemas(props.databaseId),
    ensureUsersLoaded(),
  ])

  formTriggers.value = [newTriggerItem()]
  formActions.value  = [newActionItem()]
}

async function openEdit(id: string): Promise<void> {
  const auto = automations.value.find(a => a.id === id)
  if (!auto) return

  editingId.value = id
  formName.value  = auto.name
  formError.value = null

  await Promise.all([
    dbStore.fetchAllDatabases(),
    ensureUsersLoaded(),
  ])

  // Load triggers — normalise legacy single-dict to array.
  const savedTriggers = getTriggers(auto)
  const triggerItems: FormTriggerItem[] = []

  for (const tr of savedTriggers) {
    const dbId = tr.db_uuid || props.databaseId
    await ensureDbSchemas(dbId)

    const af                   = tr.actor_filter
    const actorMode: 'any' | 'specific' = af?.mode === 'specific' ? 'specific' : 'any'
    const actorIncludeAutomation         = af?.include_automation ?? false
    const actorEntries: FormActorEntry[] = allUsers.value.map(u => {
      const found = af?.mode === 'specific' ? af.entries.find(e => e.uuid === u.id) : undefined
      return {
        uuid:     u.id,
        username: u.username,
        state:    (found?.state ?? 'unselected') as FormActorEntry['state'],
      }
    })

    triggerItems.push({
      _id: crypto.randomUUID(),
      type: 'PropertyChanged',
      dbId,
      propertyId:             tr.property_uuid || '',
      actorMode,
      actorIncludeAutomation,
      actorEntries,
    })
  }
  formTriggers.value = triggerItems.length > 0 ? triggerItems : [newTriggerItem()]

  // Load actions — support both bulk format and legacy triggered-entry format.
  const actionItems: FormActionItem[] = []

  for (const action of auto.actions) {
    // New bulk format: PUT /api/databases/<db>/bulk-values/<schema>
    const bulkM = action.endpoint.match(
      /^PUT \/api\/databases\/([a-f0-9-]{36})\/bulk-values\/([a-f0-9-]{36})$/,
    )
    if (bulkM) {
      const targetDbId       = bulkM[1]
      const targetPropertyId = bulkM[2]
      await ensureDbSchemas(targetDbId)
      const schema = dbStore.getSchemas(targetDbId).find(s => s.id === targetPropertyId)
      let valueStr = ''; let valueBool = false
      if (schema) {
        const p = parseActionValue(schema, action.body)
        valueStr = p.strVal; valueBool = p.boolVal
      }
      const sf = action.filter
      const filter: FormActionFilter = sf && sf.mode === 'where' && sf.groups.length > 0
        ? {
            mode:   'where',
            groups: sf.groups.map(g => ({
              _id:         crypto.randomUUID(),
              conjunction: g.conjunction,
              conditions:  g.filters.map(f => ({
                _id:      crypto.randomUUID(),
                schemaId: f.schemaId,
                operator: f.operator as FilterOperator,
                value:    f.value,
              })),
            })),
          }
        : { mode: 'all', groups: [] }
      actionItems.push({ _id: crypto.randomUUID(), type: 'EditProperty', targetDbId, filter, targetPropertyId, valueStr, valueBool })
      continue
    }

    // Legacy format: PUT /api/databases/<db>/entries/{trigger.entry_id}/values/<schema>
    const legM = action.endpoint.match(
      /^PUT \/api\/databases\/([a-f0-9-]{36})\/entries\/[^/]+\/values\/([a-f0-9-]{36})$/,
    )
    if (legM) {
      const targetDbId       = legM[1]
      const targetPropertyId = legM[2]
      await ensureDbSchemas(targetDbId)
      const schema = dbStore.getSchemas(targetDbId).find(s => s.id === targetPropertyId)
      let valueStr = ''; let valueBool = false
      if (schema) {
        const p = parseActionValue(schema, action.body)
        valueStr = p.strVal; valueBool = p.boolVal
      }
      actionItems.push({
        _id: crypto.randomUUID(), type: 'EditProperty', targetDbId,
        filter: { mode: 'triggered', groups: [] }, targetPropertyId, valueStr, valueBool,
      })
    }
  }
  formActions.value = actionItems.length > 0 ? actionItems : [newActionItem()]
}

function cancelForm(): void {
  editingId.value = null
  formError.value = null
}

// ── Form save ─────────────────────────────────────────────────────────────────

async function saveForm(): Promise<void> {
  formError.value = null

  if (!formName.value.trim()) {
    formError.value = t('automations.errors.nameRequired')
    return
  }

  const validActions = formActions.value.filter(a => a.targetDbId && a.targetPropertyId)
  if (validActions.length === 0) {
    formError.value = t('automations.errors.noActions')
    return
  }

  // Build trigger array (always an array, even for single triggers).
  const triggers: AutomationTrigger[] = formTriggers.value.map(ft => {
    const trigger: AutomationTrigger = {
      action_type:   'PropertyUpdate',
      origin:        'user',
      actor_uuid:    '',
      db_uuid:       ft.dbId,
      property_uuid: ft.propertyId,
      old_value:     '',
      new_value:     '',
    }
    if (ft.actorMode === 'specific') {
      const entries = ft.actorEntries
        .filter(e => e.state !== 'unselected')
        .map(e => ({ uuid: e.uuid, state: e.state as 'positive' | 'negative' }))
      trigger.actor_filter = {
        mode:               'specific',
        entries,
        include_automation: ft.actorIncludeAutomation,
      }
    }
    return trigger
  })

  // Build actions.
  // 'triggered' mode uses the legacy single-entry endpoint (no filter key).
  // 'all' and 'where' use the bulk endpoint with an explicit filter object.
  const actions: AutomationAction[] = validActions.map(a => {
    const schema = dbStore.getSchemas(a.targetDbId).find(s => s.id === a.targetPropertyId)
    const body   = schema ? buildActionBody(schema, a.valueStr, a.valueBool) : { value: null }

    if (a.filter.mode === 'triggered') {
      return {
        endpoint: `PUT /api/databases/${a.targetDbId}/entries/{trigger.entry_id}/values/${a.targetPropertyId}`,
        body,
      }
    }

    const filterGroups = a.filter.mode === 'where'
      ? a.filter.groups.map(g => ({
          conjunction: g.conjunction,
          filters:     g.conditions.map(c => ({
            schemaId: c.schemaId,
            operator: c.operator,
            value:    c.value,
          })),
        }))
      : []
    return {
      endpoint: `PUT /api/databases/${a.targetDbId}/bulk-values/${a.targetPropertyId}`,
      filter:   { mode: a.filter.mode, groups: filterGroups },
      body,
    }
  })

  saving.value = true
  try {
    if (editingId.value) {
      await store.update(editingId.value, props.databaseId, {
        name:    formName.value.trim(),
        trigger: triggers,
        actions,
      })
    } else {
      await store.create({
        database_id: props.databaseId,
        name:        formName.value.trim(),
        trigger:     triggers,
        actions,
        enabled:     true,
      })
    }
    editingId.value = null
  } catch {
    formError.value = t('automations.errors.saveFailed')
  } finally {
    saving.value = false
  }
}

// ── Bootstrap ─────────────────────────────────────────────────────────────────

onMounted(async () => {
  try {
    await store.fetchForDatabase(props.databaseId)
  } catch {
    error.value = t('automations.errors.loadFailed')
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <Teleport to="body">
    <div class="am-backdrop" @mousedown.self="emit('close')">
      <div class="am" role="dialog" :aria-label="t('automations.title')">

        <!-- ── Header ──────────────────────────────────────────────────────── -->
        <div class="am__header">
          <Icon icon="mdi:lightning-bolt" width="16" height="16" class="am__header-icon" />
          <span class="am__header-title">{{ t('automations.title') }}</span>
          <span v-if="automations.length > 0" class="am__count">{{ automations.length }}</span>
          <div class="am__header-spacer" />
          <button class="am__header-close" :title="t('actions.cancel')" @click="emit('close')">
            <Icon icon="mdi:close" width="16" height="16" />
          </button>
        </div>

        <!-- ── Body ────────────────────────────────────────────────────────── -->
        <div class="am__body">

          <!-- Loading -->
          <div v-if="loading" class="am__state">
            <span class="am__spinner" />
          </div>

          <!-- Load error -->
          <div v-else-if="error && editingId === null" class="am__state am__state--error">
            <Icon icon="mdi:alert-circle-outline" width="18" height="18" />
            {{ error }}
          </div>

          <!-- ── Form pane ──────────────────────────────────────────────────── -->
          <template v-else-if="editingId !== null">
            <div class="am__form">

              <!-- Name -->
              <div class="am__field">
                <label class="am__label">{{ t('automations.form.nameLabel') }}</label>
                <input
                  v-model="formName"
                  class="am__input"
                  :placeholder="t('automations.form.namePlaceholder')"
                  @keydown.escape.prevent="cancelForm"
                />
              </div>

              <!-- ── Trigger section ──────────────────────────────────────── -->
              <div class="am__section">
                <div class="am__section-header">
                  <Icon icon="mdi:lightning-bolt-outline" width="13" height="13" />
                  <span class="am__section-title">{{ t('automations.trigger.title') }}</span>
                </div>

                <!-- Trigger cards -->
                <div
                  v-for="(trigger, tidx) in formTriggers"
                  :key="trigger._id"
                  class="am__trigger-card"
                >
                  <!-- Header: label + remove (only when multiple triggers) -->
                  <div v-if="formTriggers.length > 1" class="am__trigger-card-header">
                    <span class="am__step-label">
                      {{ t('automations.trigger.cardLabel', { n: tidx + 1 }) }}
                    </span>
                    <button
                      class="am__ctrl-btn am__ctrl-btn--danger"
                      :title="t('automations.trigger.removeTrigger')"
                      @click="removeTrigger(tidx)"
                    >
                      <Icon icon="mdi:close" width="13" height="13" />
                    </button>
                  </div>

                  <!-- Level 1: Trigger type -->
                  <div class="am__cascade-row">
                    <span class="am__cascade-label">{{ t('automations.trigger.typeLabel') }}</span>
                    <select v-model="trigger.type" class="am__select am__select--grow">
                      <option value="PropertyChanged">{{ t('automations.trigger.typePropertyChanged') }}</option>
                    </select>
                  </div>

                  <!-- Level 2: Source database -->
                  <div class="am__cascade-row am__cascade-row--indent">
                    <span class="am__cascade-label">{{ t('automations.trigger.sourceDb') }}</span>
                    <select
                      :value="trigger.dbId"
                      class="am__select am__select--grow"
                      @change="onTriggerDbChange(tidx, ($event.target as HTMLSelectElement).value)"
                    >
                      <option
                        v-for="db in dbStore.allDatabases"
                        :key="db.id"
                        :value="db.id"
                      >{{ db.title || t('nav.untitled') }}</option>
                    </select>
                  </div>

                  <!-- Level 3: Property -->
                  <div class="am__cascade-row am__cascade-row--indent">
                    <span class="am__cascade-label">{{ t('automations.trigger.property') }}</span>
                    <select v-model="trigger.propertyId" class="am__select am__select--grow">
                      <option value="">{{ t('automations.trigger.anyProperty') }}</option>
                      <option
                        v-for="s in triggerDbSchemas(trigger.dbId)"
                        :key="s.id"
                        :value="s.id"
                      >{{ s.name }}</option>
                    </select>
                  </div>

                  <!-- Level 4: Actor filter -->
                  <div class="am__cascade-row am__cascade-row--indent">
                    <span class="am__cascade-label">{{ t('automations.trigger.actor') }}</span>
                    <div class="am__pill-toggle">
                      <button
                        class="am__pill-opt"
                        :class="{ 'am__pill-opt--active': trigger.actorMode === 'any' }"
                        @click="trigger.actorMode = 'any'"
                      >{{ t('automations.trigger.actorAny') }}</button>
                      <button
                        class="am__pill-opt"
                        :class="{ 'am__pill-opt--active': trigger.actorMode === 'specific' }"
                        @click="trigger.actorMode = 'specific'"
                      >{{ t('automations.trigger.actorSpecific') }}</button>
                    </div>
                  </div>

                  <!-- Specific actor list -->
                  <template v-if="trigger.actorMode === 'specific'">
                    <div class="am__actor-list">
                      <div
                        v-for="entry in trigger.actorEntries"
                        :key="entry.uuid"
                        class="am__actor-item"
                        :class="{
                          'am__actor-item--positive': entry.state === 'positive',
                          'am__actor-item--negative': entry.state === 'negative',
                        }"
                        @click="cycleActorState(entry)"
                      >
                        <span class="am__actor-icon">
                          <Icon v-if="entry.state === 'positive'" icon="mdi:plus-circle" width="14" height="14" />
                          <Icon v-else-if="entry.state === 'negative'" icon="mdi:minus-circle" width="14" height="14" />
                          <Icon v-else icon="mdi:circle-outline" width="14" height="14" class="am__actor-icon--muted" />
                        </span>
                        <span class="am__actor-name">{{ entry.username }}</span>
                      </div>
                      <!-- Automation placeholder (not yet functional) -->
                      <div class="am__actor-item am__actor-item--placeholder">
                        <span class="am__actor-icon am__actor-icon--muted">
                          <Icon icon="mdi:robot-outline" width="14" height="14" />
                        </span>
                        <span class="am__actor-name">{{ t('automations.trigger.actorAutomation') }}</span>
                        <input type="checkbox" v-model="trigger.actorIncludeAutomation" class="am__actor-check" disabled />
                      </div>
                    </div>
                  </template>
                </div>

                <!-- Add trigger button -->
                <button class="am__add-trigger-btn" @click="addTrigger">
                  <Icon icon="mdi:plus" width="13" height="13" />
                  {{ t('automations.trigger.addTrigger') }}
                </button>
              </div>

              <!-- ── Action section ───────────────────────────────────────── -->
              <div class="am__section">
                <div class="am__section-header">
                  <Icon icon="mdi:arrow-right-circle-outline" width="13" height="13" />
                  <span class="am__section-title">{{ t('automations.action.title') }}</span>
                </div>

                <!-- Action cards -->
                <div
                  v-for="(action, idx) in formActions"
                  :key="action._id"
                  class="am__action-card"
                >
                  <!-- Card header: step label + reorder + remove -->
                  <div class="am__action-card-header">
                    <span class="am__step-label">
                      {{ t('automations.action.stepLabel', { n: idx + 1 }) }}
                    </span>
                    <div class="am__action-controls">
                      <button class="am__ctrl-btn" :disabled="idx === 0"
                        :title="t('automations.action.moveUp')" @click="moveActionUp(idx)">
                        <Icon icon="mdi:chevron-up" width="13" height="13" />
                      </button>
                      <button class="am__ctrl-btn" :disabled="idx === formActions.length - 1"
                        :title="t('automations.action.moveDown')" @click="moveActionDown(idx)">
                        <Icon icon="mdi:chevron-down" width="13" height="13" />
                      </button>
                      <button class="am__ctrl-btn am__ctrl-btn--danger"
                        :title="t('automations.action.remove')" @click="removeAction(idx)">
                        <Icon icon="mdi:close" width="13" height="13" />
                      </button>
                    </div>
                  </div>

                  <!-- Level 1: Action type -->
                  <div class="am__cascade-row">
                    <span class="am__cascade-label">{{ t('automations.action.typeLabel') }}</span>
                    <select v-model="action.type" class="am__select am__select--grow">
                      <option value="EditProperty">{{ t('automations.action.typeEditProperty') }}</option>
                    </select>
                  </div>

                  <!-- Level 2: Target database -->
                  <div class="am__cascade-row am__cascade-row--indent">
                    <span class="am__cascade-label">{{ t('automations.action.targetDb') }}</span>
                    <select
                      :value="action.targetDbId"
                      class="am__select am__select--grow"
                      @change="onActionDbChange(idx, ($event.target as HTMLSelectElement).value)"
                    >
                      <option
                        v-for="db in dbStore.allDatabases"
                        :key="db.id"
                        :value="db.id"
                      >{{ db.title || t('nav.untitled') }}</option>
                    </select>
                  </div>

                  <!-- Level 3: Filter (all / where) -->
                  <div class="am__cascade-row am__cascade-row--indent">
                    <span class="am__cascade-label">{{ t('automations.action.filterLabel') }}</span>
                    <div class="am__pill-toggle">
                      <button
                        class="am__pill-opt"
                        :class="{ 'am__pill-opt--active': action.filter.mode === 'triggered' }"
                        @click="setActionFilterMode(idx, 'triggered')"
                      >{{ t('automations.action.filterTriggered') }}</button>
                      <button
                        class="am__pill-opt"
                        :class="{ 'am__pill-opt--active': action.filter.mode === 'all' }"
                        @click="setActionFilterMode(idx, 'all')"
                      >{{ t('automations.action.filterAll') }}</button>
                      <button
                        class="am__pill-opt"
                        :class="{ 'am__pill-opt--active': action.filter.mode === 'where' }"
                        @click="setActionFilterMode(idx, 'where')"
                      >{{ t('automations.action.filterWhere') }}</button>
                    </div>
                  </div>

                  <!-- Filter condition rows (mode === 'where') -->
                  <template v-if="action.filter.mode === 'where'">
                    <div
                      v-for="(group, gi) in action.filter.groups"
                      :key="group._id"
                      class="am__filter-group"
                    >
                      <div
                        v-for="(cond, ci) in group.conditions"
                        :key="cond._id"
                        class="am__filter-condition"
                      >
                        <!-- Property/schema picker -->
                        <select
                          :value="cond.schemaId"
                          class="am__select am__select--grow"
                          @change="onFilterCondSchemaChange(idx, gi, ci, $event)"
                        >
                          <option value="" disabled>{{ t('automations.action.pickProperty') }}</option>
                          <option
                            v-for="s in actionFilterSchemas(action.targetDbId)"
                            :key="s.id"
                            :value="s.id"
                          >{{ s.name }}</option>
                        </select>

                        <!-- Operator picker -->
                        <select
                          :value="cond.operator"
                          class="am__select am__select--op"
                          @change="onFilterCondOperatorChange(idx, gi, ci, $event)"
                        >
                          <option
                            v-for="op in filterCondOperators(idx, gi, ci)"
                            :key="op"
                            :value="op"
                          >{{ t(`automations.action.filterOp.${op}`) }}</option>
                        </select>

                        <!-- Value input (only when operator requires a value) -->
                        <template v-if="filterNeedsValue(cond.operator)">
                          <select
                            v-if="filterCondSchema(action, gi, ci)?.type === 'select'"
                            v-model="cond.value"
                            class="am__select am__select--grow"
                          >
                            <option value="">—</option>
                            <option
                              v-for="opt in filterCondSelectOptions(action, gi, ci)"
                              :key="opt.value"
                              :value="opt.value"
                            >{{ opt.label }}</option>
                          </select>
                          <select
                            v-else-if="filterCondSchema(action, gi, ci)?.type === 'checkbox'"
                            v-model="cond.value"
                            class="am__select"
                          >
                            <option value="true">{{ t('automations.action.checkboxTrue') }}</option>
                            <option value="false">{{ t('automations.action.checkboxFalse') }}</option>
                          </select>
                          <input
                            v-else-if="filterCondSchema(action, gi, ci)?.type === 'number'"
                            v-model="cond.value"
                            type="number"
                            class="am__input am__input--short"
                            :placeholder="t('automations.action.filterValuePlaceholder')"
                          />
                          <input
                            v-else
                            v-model="cond.value"
                            type="text"
                            class="am__input am__input--grow"
                            :placeholder="t('automations.action.filterValuePlaceholder')"
                          />
                        </template>

                        <!-- Remove condition -->
                        <button
                          class="am__ctrl-btn am__ctrl-btn--danger"
                          @click="removeFilterCondition(idx, gi, ci)"
                        >
                          <Icon icon="mdi:close" width="13" height="13" />
                        </button>
                      </div>

                      <!-- Add condition -->
                      <button class="am__add-filter-cond-btn" @click="addFilterCondition(idx, gi)">
                        <Icon icon="mdi:plus" width="12" height="12" />
                        {{ t('automations.action.filterAddCondition') }}
                      </button>
                    </div>
                  </template>

                  <!-- Level 4a: Property to set -->
                  <div class="am__cascade-row am__cascade-row--indent">
                    <span class="am__cascade-label">{{ t('automations.action.setProperty') }}</span>
                    <select
                      :value="action.targetPropertyId"
                      class="am__select am__select--grow"
                      @change="onActionPropertyChange(idx, ($event.target as HTMLSelectElement).value)"
                    >
                      <option value="" disabled>{{ t('automations.action.pickProperty') }}</option>
                      <option
                        v-for="s in actionTargetSchemas(action.targetDbId)"
                        :key="s.id"
                        :value="s.id"
                      >{{ s.name }}</option>
                    </select>
                  </div>

                  <!-- Level 4b: New value (type-aware, only when property selected) -->
                  <template v-if="actionTargetSchema(action)">
                    <div class="am__cascade-row am__cascade-row--indent2">
                      <span class="am__cascade-label">{{ t('automations.action.toValue') }}</span>

                      <select
                        v-if="actionTargetSchema(action)!.type === 'select'"
                        v-model="action.valueStr"
                        class="am__select am__select--grow"
                      >
                        <option value="">{{ t('automations.action.noValue') }}</option>
                        <option
                          v-for="opt in actionSelectOptions(action)"
                          :key="opt.value"
                          :value="opt.value"
                        >{{ opt.label }}</option>
                      </select>

                      <div
                        v-else-if="actionTargetSchema(action)!.type === 'checkbox'"
                        class="am__bool-toggle"
                      >
                        <button class="am__bool-btn"
                          :class="{ 'am__bool-btn--active': action.valueBool === true }"
                          @click="action.valueBool = true"
                        >{{ t('automations.action.checkboxTrue') }}</button>
                        <button class="am__bool-btn"
                          :class="{ 'am__bool-btn--active': action.valueBool === false }"
                          @click="action.valueBool = false"
                        >{{ t('automations.action.checkboxFalse') }}</button>
                      </div>

                      <input
                        v-else-if="actionTargetSchema(action)!.type === 'number'"
                        v-model="action.valueStr"
                        type="number"
                        class="am__input am__input--short"
                        :placeholder="t('automations.action.valuePlaceholder')"
                      />

                      <input
                        v-else
                        v-model="action.valueStr"
                        type="text"
                        class="am__input am__input--grow"
                        :placeholder="t('automations.action.valuePlaceholder')"
                      />
                    </div>
                  </template>
                </div>

                <!-- Add action button -->
                <button class="am__add-action-btn" @click="addAction">
                  <Icon icon="mdi:plus" width="13" height="13" />
                  {{ t('automations.action.addAction') }}
                </button>
              </div>

              <!-- Form error -->
              <p v-if="formError" class="am__form-error">
                <Icon icon="mdi:alert-circle-outline" width="13" height="13" />
                {{ formError }}
              </p>

              <!-- Form footer -->
              <div class="am__form-footer">
                <button class="am__btn am__btn--ghost" @click="cancelForm">
                  {{ t('actions.cancel') }}
                </button>
                <button class="am__btn am__btn--primary" :disabled="saving" @click="saveForm">
                  <Icon v-if="saving" icon="mdi:loading" width="13" height="13" class="am__spin" />
                  {{ editingId ? t('actions.save') : t('automations.form.create') }}
                </button>
              </div>
            </div>
          </template>

          <!-- ── List pane ──────────────────────────────────────────────────── -->
          <template v-else>
            <div v-if="automations.length === 0" class="am__empty">
              <Icon icon="mdi:lightning-bolt-outline" width="32" height="32" class="am__empty-icon" />
              <p class="am__empty-text">{{ t('automations.empty') }}</p>
              <p class="am__empty-hint">{{ t('automations.emptyHint') }}</p>
            </div>

            <ul v-else class="am__list">
              <li
                v-for="auto in automations"
                :key="auto.id"
                class="am__item"
                :class="{ 'am__item--disabled': !auto.enabled }"
              >
                <button
                  class="am__toggle"
                  :class="{ 'am__toggle--on': auto.enabled }"
                  :title="auto.enabled ? t('automations.disable') : t('automations.enable')"
                  @click="onToggle(auto.id)"
                >
                  <span class="am__toggle-knob" />
                </button>

                <div class="am__item-info" @click="openEdit(auto.id)">
                  <span class="am__item-name">{{ auto.name }}</span>
                  <span class="am__item-trigger">{{ triggerSummary(auto) }}</span>
                </div>

                <button class="am__item-btn" :title="t('actions.edit')" @click="openEdit(auto.id)">
                  <Icon icon="mdi:pencil-outline" width="14" height="14" />
                </button>

                <button
                  class="am__item-btn"
                  :class="{ 'am__item-btn--danger': confirmDeleteId === auto.id }"
                  :title="confirmDeleteId === auto.id ? t('automations.deleteConfirm') : t('actions.delete')"
                  @click="requestDelete(auto.id)"
                >
                  <Icon
                    :icon="confirmDeleteId === auto.id ? 'mdi:check' : 'mdi:trash-can-outline'"
                    width="14" height="14"
                  />
                </button>
              </li>
            </ul>

            <button class="am__new-btn" @click="openCreate">
              <Icon icon="mdi:plus" width="14" height="14" />
              {{ t('automations.new') }}
            </button>
          </template>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
/* ── Backdrop & shell ──────────────────────────────────────────────────────── */
.am-backdrop {
  position: fixed;
  inset: 0;
  z-index: 500;
  background: rgba(0, 0, 0, 0.35);
  display: flex;
  align-items: center;
  justify-content: center;
}

.am {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  box-shadow: 0 16px 48px rgba(0, 0, 0, 0.28);
  width: 540px;
  max-width: calc(100vw - 32px);
  max-height: calc(100vh - 64px);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* ── Header ────────────────────────────────────────────────────────────────── */
.am__header {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 14px 16px 12px;
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.am__header-icon   { color: var(--color-accent); flex-shrink: 0; }
.am__header-spacer { flex: 1; }

.am__header-title {
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--color-text);
}

.am__count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 9px;
  background: var(--color-accent-subtle);
  color: var(--color-accent);
  font-size: 0.7rem;
  font-weight: 700;
}

.am__header-close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border: none;
  border-radius: 5px;
  background: transparent;
  cursor: pointer;
  color: var(--color-text-muted);
  transition: background 0.12s, color 0.12s;
}

.am__header-close:hover { background: var(--color-hover); color: var(--color-text); }

/* ── Body ──────────────────────────────────────────────────────────────────── */
.am__body { flex: 1; overflow-y: auto; padding: 0; }

/* ── States ────────────────────────────────────────────────────────────────── */
.am__state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 40px 16px;
  color: var(--color-text-muted);
  font-size: 0.82rem;
}

.am__state--error { color: #e05555; }

.am__spinner {
  width: 20px;
  height: 20px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-accent);
  border-radius: 50%;
  animation: am-spin 0.7s linear infinite;
}

@keyframes am-spin { to { transform: rotate(360deg); } }
.am__spin { animation: am-spin 0.7s linear infinite; }

/* ── Empty state ───────────────────────────────────────────────────────────── */
.am__empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 36px 20px 24px;
  text-align: center;
}

.am__empty-icon  { color: var(--color-border); }

.am__empty-text {
  font-size: 0.85rem;
  font-weight: 500;
  color: var(--color-text-muted);
  margin: 4px 0 0;
}

.am__empty-hint {
  font-size: 0.78rem;
  color: var(--color-text-muted);
  opacity: 0.7;
  margin: 0;
}

/* ── List ──────────────────────────────────────────────────────────────────── */
.am__list { list-style: none; margin: 0; padding: 6px 0; }

.am__item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 14px;
  transition: background 0.1s;
}

.am__item:hover        { background: var(--color-hover); }
.am__item--disabled    { opacity: 0.55; }

.am__toggle {
  position: relative;
  width: 30px;
  height: 17px;
  border-radius: 9px;
  border: none;
  background: var(--color-border);
  cursor: pointer;
  flex-shrink: 0;
  transition: background 0.18s;
  padding: 0;
}

.am__toggle--on { background: var(--color-accent); }

.am__toggle-knob {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 13px;
  height: 13px;
  border-radius: 50%;
  background: #fff;
  transition: transform 0.18s;
  display: block;
}

.am__toggle--on .am__toggle-knob { transform: translateX(13px); }

.am__item-info {
  flex: 1;
  min-width: 0;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.am__item-name {
  font-size: 0.82rem;
  font-weight: 500;
  color: var(--color-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.am__item-trigger {
  font-size: 0.74rem;
  color: var(--color-text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.am__item-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border: none;
  border-radius: 5px;
  background: transparent;
  cursor: pointer;
  color: var(--color-text-muted);
  flex-shrink: 0;
  opacity: 0;
  transition: background 0.12s, color 0.12s, opacity 0.12s;
}

.am__item:hover .am__item-btn  { opacity: 1; }
.am__item-btn:hover            { background: var(--color-hover); color: var(--color-text); }
.am__item-btn--danger          { color: #e05555; }
.am__item-btn--danger:hover    { background: rgba(224, 85, 85, 0.1); }

.am__new-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 10px 14px;
  border: none;
  border-top: 1px solid var(--color-border);
  background: transparent;
  cursor: pointer;
  font-size: 0.82rem;
  color: var(--color-accent);
  transition: background 0.12s;
  margin-top: 2px;
}

.am__new-btn:hover { background: var(--color-accent-subtle); }

/* ── Form ──────────────────────────────────────────────────────────────────── */
.am__form {
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.am__field { display: flex; flex-direction: column; gap: 5px; }

.am__label {
  font-size: 0.74rem;
  font-weight: 600;
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.am__input {
  padding: 7px 10px;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-bg);
  color: var(--color-text);
  font-size: 0.85rem;
  outline: none;
  transition: border-color 0.15s;
  width: 100%;
  box-sizing: border-box;
}

.am__input:focus       { border-color: var(--color-accent); }
.am__input--short      { width: 120px; }
.am__input--grow       { flex: 1; min-width: 0; }

.am__select {
  padding: 6px 8px;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-bg);
  color: var(--color-text);
  font-size: 0.82rem;
  cursor: pointer;
  outline: none;
  min-width: 0;
}

.am__select:focus  { border-color: var(--color-accent); }
.am__select--grow  { flex: 1; }
.am__select--op    { min-width: 80px; max-width: 120px; }

/* ── Section ───────────────────────────────────────────────────────────────── */
.am__section {
  background: var(--color-hover);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.am__section-header {
  display: flex;
  align-items: center;
  gap: 5px;
  color: var(--color-text-muted);
}

.am__section-title {
  font-size: 0.72rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

/* ── Cascade rows ──────────────────────────────────────────────────────────── */
.am__cascade-row          { display: flex; align-items: center; gap: 8px; }
.am__cascade-row--indent  { padding-left: 14px; }
.am__cascade-row--indent2 { padding-left: 28px; }

.am__cascade-label {
  font-size: 0.78rem;
  color: var(--color-text-muted);
  white-space: nowrap;
  flex-shrink: 0;
  min-width: 68px;
}

/* ── Pill toggle ───────────────────────────────────────────────────────────── */
.am__pill-toggle {
  display: flex;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  overflow: hidden;
}

.am__pill-opt {
  padding: 4px 12px;
  border: none;
  background: transparent;
  cursor: pointer;
  font-size: 0.78rem;
  color: var(--color-text-muted);
  transition: background 0.12s, color 0.12s;
}

.am__pill-opt--active { background: var(--color-accent); color: #fff; }

/* ── Actor list ────────────────────────────────────────────────────────────── */
.am__actor-list {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding-left: 14px;
  padding-top: 2px;
}

.am__actor-item {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 4px 8px;
  border-radius: 5px;
  cursor: pointer;
  font-size: 0.82rem;
  color: var(--color-text-muted);
  user-select: none;
  transition: background 0.1s, color 0.1s;
}

.am__actor-item:hover                         { background: rgba(0, 0, 0, 0.04); }
.am__actor-item--positive .am__actor-icon     { color: var(--color-accent); }
.am__actor-item--positive                     { color: var(--color-text); }
.am__actor-item--negative .am__actor-icon     { color: #e05555; }
.am__actor-item--negative                     { color: var(--color-text); }
.am__actor-item--placeholder                  { opacity: 0.45; cursor: default; }
.am__actor-item--placeholder:hover            { background: transparent; }

.am__actor-icon       { display: flex; align-items: center; flex-shrink: 0; color: var(--color-border); }
.am__actor-icon--muted{ color: var(--color-border); }
.am__actor-name       { flex: 1; }
.am__actor-check      { cursor: not-allowed; flex-shrink: 0; }

/* ── Trigger cards ─────────────────────────────────────────────────────────── */
.am__trigger-card {
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 7px;
  padding: 9px 11px;
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.am__trigger-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.am__add-trigger-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  padding: 5px 10px;
  border: 1px dashed var(--color-border);
  border-radius: 6px;
  background: transparent;
  cursor: pointer;
  font-size: 0.78rem;
  color: var(--color-accent);
  transition: background 0.12s, border-color 0.12s;
  width: 100%;
}

.am__add-trigger-btn:hover {
  background: var(--color-accent-subtle);
  border-color: var(--color-accent);
}

/* ── Action cards ──────────────────────────────────────────────────────────── */
.am__action-card {
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 7px;
  padding: 9px 11px;
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.am__action-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.am__step-label {
  font-size: 0.7rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--color-text-muted);
}

.am__action-controls { display: flex; gap: 2px; }

.am__ctrl-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: none;
  border-radius: 4px;
  background: transparent;
  cursor: pointer;
  color: var(--color-text-muted);
  transition: background 0.12s, color 0.12s;
}

.am__ctrl-btn:hover:not(:disabled) { background: var(--color-hover); color: var(--color-text); }
.am__ctrl-btn:disabled             { opacity: 0.3; cursor: not-allowed; }
.am__ctrl-btn--danger:hover:not(:disabled) { background: rgba(224, 85, 85, 0.1); color: #e05555; }

/* ── Filter conditions ─────────────────────────────────────────────────────── */
.am__filter-group {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding-left: 14px;
}

.am__filter-condition {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.am__add-filter-cond-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  border: 1px dashed var(--color-border);
  border-radius: 5px;
  background: transparent;
  cursor: pointer;
  font-size: 0.75rem;
  color: var(--color-text-muted);
  transition: background 0.12s, border-color 0.12s, color 0.12s;
  align-self: flex-start;
}

.am__add-filter-cond-btn:hover {
  background: var(--color-accent-subtle);
  border-color: var(--color-accent);
  color: var(--color-accent);
}

/* ── Add action button ─────────────────────────────────────────────────────── */
.am__add-action-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  padding: 6px 10px;
  border: 1px dashed var(--color-border);
  border-radius: 6px;
  background: transparent;
  cursor: pointer;
  font-size: 0.78rem;
  color: var(--color-accent);
  transition: background 0.12s, border-color 0.12s;
  width: 100%;
}

.am__add-action-btn:hover { background: var(--color-accent-subtle); border-color: var(--color-accent); }

/* ── Bool toggle ───────────────────────────────────────────────────────────── */
.am__bool-toggle {
  display: flex;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  overflow: hidden;
}

.am__bool-btn {
  padding: 5px 12px;
  border: none;
  background: transparent;
  cursor: pointer;
  font-size: 0.8rem;
  color: var(--color-text-muted);
  transition: background 0.12s, color 0.12s;
}

.am__bool-btn--active { background: var(--color-accent); color: #fff; }

/* ── Form error & footer ───────────────────────────────────────────────────── */
.am__form-error {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 0.78rem;
  color: #e05555;
  margin: 0;
}

.am__form-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 4px;
  border-top: 1px solid var(--color-border);
}

.am__btn {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 6px 14px;
  border-radius: 6px;
  border: none;
  font-size: 0.82rem;
  cursor: pointer;
  transition: background 0.12s, opacity 0.12s;
}

.am__btn:disabled { opacity: 0.5; cursor: not-allowed; }

.am__btn--ghost {
  background: transparent;
  color: var(--color-text-muted);
  border: 1px solid var(--color-border);
}

.am__btn--ghost:hover { background: var(--color-hover); color: var(--color-text); }

.am__btn--primary { background: var(--color-accent); color: #fff; }
.am__btn--primary:not(:disabled):hover { opacity: 0.85; }
</style>

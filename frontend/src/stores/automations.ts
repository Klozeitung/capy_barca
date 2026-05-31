/**
 * Automations store
 *
 * Client-side cache and CRUD operations for the automations feature.
 * Each automation is scoped to a database block and consists of one or more
 * triggers (when) and an ordered list of actions (then).
 *
 * Multiple triggers are stored as an array in the ``trigger`` JSON column;
 * the engine fires when ANY trigger matches (OR semantics).  Legacy automations
 * with a single trigger dict are normalised to an array via ``getTriggers()``.
 *
 * The cache is keyed by database_id.  Mutations keep the cache consistent
 * without requiring a full re-fetch after every write.
 *
 * Usage:
 *   const store = useAutomationsStore()
 *   await store.fetchForDatabase(databaseId)
 *   const list = store.getForDatabase(databaseId)
 *   const triggers = getTriggers(list[0])
 */
import { ref } from 'vue'
import { defineStore } from 'pinia'
import { apiClient } from '@/api/client'

// ── Types ─────────────────────────────────────────────────────────────────────

/**
 * A single entry in the actor_filter entries list.
 *
 * ``state: "positive"``  – the automation only fires when the actor matches.
 * ``state: "negative"``  – the automation is suppressed when the actor matches.
 */
export interface ActorFilterEntry {
  uuid:  string
  state: 'positive' | 'negative'
}

/**
 * Extended actor-matching descriptor stored as an optional field on the
 * trigger JSON.  When present and mode == "specific", the engine evaluates
 * the entries list in addition to the base actor_uuid wildcard check.
 *
 * mode == "any"      – no filtering; any actor passes (default behaviour).
 * mode == "specific" – positive entries form an allow-list; negative entries
 *                      form a deny-list.  Both lists are evaluated together:
 *                        - If positive entries exist, actor must match one.
 *                        - Actor must not match any negative entry.
 *
 * include_automation – reserved for future use; controls whether
 *                      automation-originated events are eligible.
 */
export interface ActorFilter {
  mode:               'any' | 'specific'
  entries:            ActorFilterEntry[]
  include_automation: boolean
}

/**
 * Seven-slot trigger descriptor stored on the backend.
 *
 * Every scalar field supports three matching modes:
 *   ""         – wildcard; matches any event value
 *   "!<value>" – negation; fires when the event value differs from <value>
 *   "<value>"  – exact match
 *
 * actor_filter is an optional extension for multi-user actor matching.
 * When absent, actor_uuid is the sole actor gate (empty string = wildcard).
 */
export interface AutomationTrigger {
  action_type:   string  // e.g. "PropertyUpdate"
  origin:        string  // "user" | "automation" | ""
  actor_uuid:    string  // always "" when actor_filter is used; kept for compat
  db_uuid:       string  // database UUID or ""
  property_uuid: string  // property schema UUID or ""
  old_value:     string  // previous cell value (serialised) or ""
  new_value:     string  // new cell value (serialised) or ""
  actor_filter?: ActorFilter  // optional extended actor matching
}

/**
 * A single filter condition inside an action filter group.
 *
 * Mirrors the ViewFilter shape (schemaId, operator, value) without the
 * date-specific fields, since automation action filters only support the
 * simple scalar operators for the initial implementation.
 */
export interface AutomationActionFilterCondition {
  schemaId: string
  operator: string
  value:    string
}

/**
 * A group of filter conditions with a shared conjunction.
 * Multiple groups in an action filter are ANDed together.
 */
export interface AutomationActionFilterGroup {
  conjunction: 'and' | 'or'
  filters:     AutomationActionFilterCondition[]
}

/**
 * Inline filter stored on an EditProperty action.
 *
 * mode == "all"   – apply to every active entry in the target database.
 * mode == "where" – apply only to entries whose property values match all groups.
 */
export interface AutomationActionFilter {
  mode:   'all' | 'where'
  groups: AutomationActionFilterGroup[]
}

/**
 * A single action executed when the trigger fires.
 *
 * ``endpoint`` follows the pattern "METHOD /api/path" and may contain
 * template variables resolved at runtime by the engine:
 *
 *   {trigger.entry_id}       – UUID of the entry that fired the trigger
 *   {trigger.db_uuid}        – database UUID from the event
 *   {trigger.property_uuid}  – property schema UUID from the event
 *   {trigger.new_value}      – new cell value from the event
 *   {today()}                – current date as YYYY-MM-DD
 *
 * ``filter`` is present on EditProperty actions (bulk endpoint format).
 * Legacy SetProperty actions (triggered-entry endpoint) have no filter key.
 */
export interface AutomationAction {
  endpoint: string
  body:     Record<string, unknown>
  filter?:  AutomationActionFilter
}

/**
 * A full automation record as returned by the backend.
 *
 * ``trigger`` may be:
 *   - a single AutomationTrigger dict (legacy, saved before multi-trigger support)
 *   - an array of AutomationTrigger objects (new format; engine applies OR logic)
 *
 * Always use ``getTriggers()`` to access triggers in a normalised form.
 */
export interface Automation {
  id:          string
  database_id: string
  name:        string
  enabled:     boolean
  trigger:     AutomationTrigger | AutomationTrigger[]
  actions:     AutomationAction[]
}

/**
 * Normalise the ``trigger`` field of an automation to an array.
 * Handles both the legacy single-dict format and the new array format.
 */
export function getTriggers(auto: Pick<Automation, 'trigger'>): AutomationTrigger[] {
  return Array.isArray(auto.trigger) ? auto.trigger : [auto.trigger]
}

// ── Store ─────────────────────────────────────────────────────────────────────

export const useAutomationsStore = defineStore('automations', () => {
  /** Cache: database_id → automation list */
  const _byDatabase = ref<Record<string, Automation[]>>({})

  // ── Read ───────────────────────────────────────────────────────────────────

  function getForDatabase(databaseId: string): Automation[] {
    return _byDatabase.value[databaseId] ?? []
  }

  async function fetchForDatabase(databaseId: string): Promise<void> {
    const list = await apiClient.get<Automation[]>(
      `/api/automations?database_id=${databaseId}`,
    )
    _byDatabase.value[databaseId] = list
  }

  // ── Write ──────────────────────────────────────────────────────────────────

  async function create(payload: {
    database_id: string
    name:        string
    trigger:     AutomationTrigger | AutomationTrigger[]
    actions:     AutomationAction[]
    enabled?:    boolean
  }): Promise<Automation> {
    const created = await apiClient.post<Automation>('/api/automations', payload)
    const list = _byDatabase.value[payload.database_id] ?? []
    _byDatabase.value[payload.database_id] = [...list, created]
    return created
  }

  async function update(
    id:         string,
    databaseId: string,
    payload: {
      name?:    string
      trigger?: AutomationTrigger | AutomationTrigger[]
      actions?: AutomationAction[]
      enabled?: boolean
    },
  ): Promise<Automation> {
    const updated = await apiClient.patch<Automation>(`/api/automations/${id}`, payload)
    _byDatabase.value[databaseId] = (
      _byDatabase.value[databaseId] ?? []
    ).map(a => (a.id === id ? updated : a))
    return updated
  }

  async function toggle(id: string, databaseId: string): Promise<Automation> {
    const updated = await apiClient.patch<Automation>(
      `/api/automations/${id}/toggle`,
      {},
    )
    _byDatabase.value[databaseId] = (
      _byDatabase.value[databaseId] ?? []
    ).map(a => (a.id === id ? updated : a))
    return updated
  }

  async function remove(id: string, databaseId: string): Promise<void> {
    await apiClient.delete(`/api/automations/${id}`)
    _byDatabase.value[databaseId] = (
      _byDatabase.value[databaseId] ?? []
    ).filter(a => a.id !== id)
  }

  return { getForDatabase, fetchForDatabase, create, update, toggle, remove }
})

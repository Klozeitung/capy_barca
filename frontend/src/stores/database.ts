/**
 * database store
 *
 * Manages property schemas, database entries, and cell values for blocks of
 * type "database". Backed by the /api/databases/* endpoints.
 *
 * State structure
 * ---------------
 * schemas      – keyed by databaseId → ordered list of PropertySchema
 * entries      – keyed by databaseId → the complete, unpaginated entry list,
 *                present only once ``fetchEntries`` has loaded it
 * queryResults – keyed by a caller-chosen result key → the entries one view
 *                has loaded so far, see ``loadViewPage``
 * entryDetails – keyed by entry ID → single entries loaded by ``fetchEntry``
 *                for the side view and the entry page
 * allDatabases – flat list of DatabaseInfo (id + title), used by relation pickers
 *
 * The two entry caches are kept apart on purpose. A view shows a page of its
 * database; a relation picker or an export needs the whole database or a
 * search across it. When both shared one cache, a view's page silently became
 * everything the picker could see.
 *
 * View types (DatabaseView, ViewFilter, ViewSort) are defined here so that
 * DatabaseBlock and any future view-aware components share a single source of
 * truth. The views themselves live in the databaseViews store.
 *
 * Relations
 * ---------
 * View pages and single entries arrive with every relation they hold already
 * resolved. ``ingestResolution`` puts that into the chip caches, so relation
 * cells find their titles and key values in place and ask the server only for
 * ids they learn of on their own, for instance after linking a new entry.
 *
 * Invalidation
 * ------------
 * After any mutation the affected collection is re-fetched from the server.
 * This keeps the implementation simple and correct; optimistic updates would
 * complicate conflict handling for schema renames and position reordering.
 * The exceptions are cell values (``upsertValue``) and entry titles, which
 * arrive over the WebSocket: both are patched in place in every cached copy
 * of the entry, so views, side panels and relation chips agree immediately.
 */
import { ref, onScopeDispose } from 'vue'
import { defineStore } from 'pinia'
import { apiClient } from '@/api/client'
import { useDatabaseTemplatesStore } from '@/stores/databaseTemplates'
import { useBlockStore } from '@/stores/blocks'
import { WS_BLOCK_EVENT, type BlockEventPayload } from '@/stores/ws'
import { useDatabaseViewsStore } from '@/stores/databaseViews'

/** Pseudo schema id of the lead property, the entry title shown as "Name". */
export const NAME_COL_KEY = '__name__'

/** Page size for the entry search used by relation pickers. */
export const ENTRY_SEARCH_LIMIT = 50

// ── Domain types ──────────────────────────────────────────────────────────────

export interface PropertySchema {
  id: string
  database_id: string
  name: string
  type: string
  config: Record<string, unknown> | null
  position: number
  /** Property group name. Default: "Standard". Used for side-panel grouping. */
  group: string
}

// ── Select option types ───────────────────────────────────────────────────────

export const SELECT_OPTION_COLORS = [
  { key: 'default', label: 'Default' },
  { key: 'gray',    label: 'Gray'    },
  { key: 'red',     label: 'Red'     },
  { key: 'orange',  label: 'Orange'  },
  { key: 'yellow',  label: 'Yellow'  },
  { key: 'green',   label: 'Green'   },
  { key: 'blue',    label: 'Blue'    },
  { key: 'purple',  label: 'Purple'  },
  { key: 'pink',    label: 'Pink'    },
] as const

export type SelectColorKey = typeof SELECT_OPTION_COLORS[number]['key']

/**
 * A single option of a select / multi-select property or of a relation's
 * nuance. Stored in ``config.options`` (``config.nuance.options`` for
 * nuances).
 *
 * Values store the ``id``, never the label, so an option can be renamed
 * without touching any value. The backend assigns ids when a config is
 * saved; the frontend gives a new option an id right away so it can be
 * selected in the same step.
 */
export interface SelectOption {
  id: string
  label: string
  color?: SelectColorKey
}

/**
 * Normalize a raw config option into a SelectOption. Safe to call on any
 * value from an option list. A plain string, or an option without id, uses
 * its label as id; that only happens for configs the backend has not stored
 * yet.
 */
export function normalizeSelectOption(opt: unknown): SelectOption {
  if (typeof opt === 'string') return { id: opt, label: opt }
  const o = (opt ?? {}) as Partial<SelectOption>
  const label = String(o.label ?? '')
  const id = typeof o.id === 'string' && o.id ? o.id : label
  return o.color ? { id, label, color: o.color } : { id, label }
}

/** The options a select property offers, in their configured order. */
export function schemaOptions(schema: Pick<PropertySchema, 'config'> | null | undefined): SelectOption[] {
  const raw = schema?.config?.options
  return Array.isArray(raw) ? raw.map(normalizeSelectOption) : []
}

/** The nuance options of a relation property, in their configured order. */
export function nuanceOptions(schema: Pick<PropertySchema, 'config'> | null | undefined): SelectOption[] {
  const nuance = schema?.config?.nuance as Record<string, unknown> | undefined
  const raw = nuance?.options
  return Array.isArray(raw) ? raw.map(normalizeSelectOption) : []
}

/** The option with *id*, or undefined when the list has none. */
export function findOption(options: SelectOption[], id: unknown): SelectOption | undefined {
  return typeof id === 'string' ? options.find((o) => o.id === id) : undefined
}

/** The label of the option with *id*, or '' when the list has none. */
export function optionLabel(options: SelectOption[], id: unknown): string {
  return findOption(options, id)?.label ?? ''
}

/** A fresh id for an option created on the client. */
export function newOptionId(): string {
  return crypto.randomUUID()
}

/**
 * Return inline CSS style (background-color + border-color) for a chip
 * with the given color key. Falls back to 'default' for unknown keys.
 */
export function optionColorStyle(colorKey?: string): Record<string, string> {
  const palette: Record<string, { bg: string; border: string }> = {
    default: { bg: 'var(--color-accent-subtle)', border: 'var(--color-accent)'        },
    gray:    { bg: 'rgba(120,120,120,0.12)',     border: 'rgba(120,120,120,0.35)'     },
    red:     { bg: 'rgba(220,70,70,0.13)',       border: 'rgba(220,70,70,0.40)'       },
    orange:  { bg: 'rgba(220,140,40,0.13)',      border: 'rgba(220,140,40,0.40)'      },
    yellow:  { bg: 'rgba(200,185,20,0.13)',      border: 'rgba(200,185,20,0.40)'      },
    green:   { bg: 'rgba(50,180,80,0.13)',       border: 'rgba(50,180,80,0.40)'       },
    blue:    { bg: 'rgba(45,130,210,0.13)',      border: 'rgba(45,130,210,0.40)'      },
    purple:  { bg: 'rgba(140,80,200,0.13)',      border: 'rgba(140,80,200,0.40)'      },
    pink:    { bg: 'rgba(220,80,160,0.13)',      border: 'rgba(220,80,160,0.40)'      },
  }
  const c = palette[colorKey ?? 'default'] ?? palette['default']
  return { 'background-color': c.bg, 'border-color': c.border }
}

export interface DatabaseEntry {
  id: string
  position: number
  content: Record<string, unknown> | null
  icon: string | null
  state: string
  /** Maps schema_id → stored value dict. A missing key means no value written yet. */
  values: Record<string, Record<string, unknown> | null>
}

/**
 * Lightweight database descriptor returned by GET /api/databases.
 * Used to populate relation-property target-database pickers.
 */
export interface DatabaseInfo {
  id: string
  title: string | null
}

/**
 * Lightweight relation-target descriptor returned by
 * POST /api/databases/{id}/entries/resolve-titles.
 *
 * Relation cells store only the linked entry IDs; their chips need a title.
 * Resolving titles from the paginated `entries` cache breaks once a relation
 * points past a database's display limit, so these descriptors are fetched and
 * cached separately, independently of any pagination.
 */
export interface RelationTitle {
  id: string
  title: string | null
  database_id: string
  /**
   * Raw value of the property requested via ``value_schema_id``, present only
   * on a key-value resolve. Null when none was requested, the property does
   * not resolve, or the entry stores no value for it.
   */
  value?: Record<string, unknown> | null
}

/**
 * One relation property that is keyed on a given property of another database.
 *
 * Returned by GET /schemas/{id}/key-references. Deleting or retyping that
 * property resets every relation listed here to vanilla, so the delete
 * confirmation can name them instead of warning in the abstract.
 */
export interface KeyReference {
  schema_id: string
  schema_name: string
  database_id: string
  database_title: string | null
}

// ── View types ────────────────────────────────────────────────────────────────

/**
 * All supported view type identifiers.
 * 'table' and 'calendar' are currently implemented; all others are planned.
 */
export type ViewType =
  | 'table'
  | 'calendar'
  | 'list'
  | 'gallery'
  | 'board'
  | 'family_tree'
  | 'mindmap'
  | 'graph'

/**
 * Column-level aggregation function for table group footers.
 * 'none'  – no aggregation shown (default)
 * 'count' – count of non-empty values in the group
 * 'sum'   – numeric sum (number / formula columns only)
 * 'avg'   – numeric average
 * 'min'   – numeric minimum
 * 'max'   – numeric maximum
 *
 * Stored in DatabaseView.columnAggregations keyed by column key
 * ('__name__' or schema ID). Only rendered when groupBySchemaId is set.
 */
export type AggregationType = 'none' | 'count' | 'sum' | 'avg' | 'min' | 'max'

export type FilterOperator =
  // Equality
  | 'eq'
  | 'neq'
  // Text
  | 'contains'
  | 'not_contains'
  | 'starts_with'
  | 'ends_with'
  // Emptiness
  | 'is_empty'
  | 'is_not_empty'
  // Numeric / date comparison
  | 'gt'
  | 'gte'
  | 'lt'
  | 'lte'
  // Date range presets (no value required; match if the date falls within the window)
  | 'past_week'
  | 'past_month'
  | 'past_year'
  | 'this_week'
  | 'next_week'
  | 'next_month'
  | 'next_year'
  // Explicit date range: value = start (inclusive), value2 = end (inclusive)
  | 'between'

/**
 * Controls how the comparison target is derived for date-type filter rows
 * that use a point-comparison operator (eq / gt / gte / lt / lte).
 *
 * 'exact'    – compare against the ISO date string stored in `value`.
 * 'today'    – compare against today's date; `value` is ignored.
 * 'relative' – compare against today ± `dateOffset` days; `value` is ignored.
 *
 * Range-preset operators (past_week, this_week, …) do not use dateMode at all;
 * the operator itself encodes the full match logic.
 */
export type DateFilterMode = 'exact' | 'today' | 'relative'

export interface ViewFilter {
  /** Stable UUID for v-for keying. */
  id: string
  /** Schema ID or NAME_COL_KEY ('__name__') for the name column. */
  schemaId: string
  operator: FilterOperator
  /** Serialised filter value; empty string for operators that need no value. */
  value: string
  /**
   * Only meaningful for date / created_time / last_edited_time schemas when
   * the operator is one of eq / gt / gte / lt / lte.
   * Defaults to 'exact' when absent (backwards-compatible).
   */
  dateMode?: DateFilterMode
  /**
   * Only meaningful when dateMode === 'relative'.
   * Positive = future, negative = past. Defaults to 0.
   */
  dateOffset?: number
  /**
   * Only meaningful when operator === 'between'.
   * ISO date string for the upper bound (inclusive). lower bound is `value`.
   */
  value2?: string
}

export interface ViewSort {
  /** Stable UUID for v-for keying. */
  id: string
  /** Schema ID or NAME_COL_KEY ('__name__') for the name column. */
  schemaId: string
  direction: 'asc' | 'desc'
}

/**
 * A group of filter conditions with a shared conjunction.
 *
 * conjunction – 'and': ALL filters in the group must match.
 *               'or':  ANY filter in the group must match.
 *
 * Multiple groups in a view are always ANDed together.
 * Example: (status=A OR status=B) AND (kind=X AND active=true)
 * is expressed as two groups: first with 'or', second with 'and'.
 */
export interface FilterGroup {
  /** Stable UUID for v-for keying. */
  id: string
  conjunction: 'and' | 'or'
  filters: ViewFilter[]
}

/**
 * A database view in the shape the components work with. The backend stores
 * ``name``, ``viewType``, ``description``, ``filterGroups`` and ``sorts`` as
 * fields of their own and everything else under ``settings``; the
 * databaseViews store translates between the two.
 *
 * ``name`` is empty for a view that was never named; ``viewDisplayName``
 * supplies the localized default for it.
 *
 * ``colOrder`` – ordered list of column keys rendered by the table.
 *   '__name__' is always the first element (the Name column).
 *   All other elements are PropertySchema IDs.
 *   Schemas not present in colOrder (added after view creation) are appended
 *   at the end; stale IDs (deleted schemas) are silently skipped.
 *
 * ``colWidths`` – maps column key → pixel width, persisted after every resize.
 * ``hiddenColumns`` – list of column keys hidden in this view: schema IDs and,
 *   when the Name column is hidden, '__name__'. Readonly system schemas are
 *   hidden by default in every new view; the Name column is shown by default.
 */
export interface DatabaseView {
  id: string
  name: string
  /** Determines which renderer is used for this view. */
  viewType: ViewType
  colOrder: string[]
  colWidths: Record<string, number>
  /**
   * Filter groups. Each group has its own conjunction; groups are ANDed
   * together. This supersedes the legacy `filters` flat list.
   */
  filterGroups: FilterGroup[]
  sorts: ViewSort[]
  /** Column keys (schema IDs or '__name__') that are not rendered in this view. */
  hiddenColumns: string[]
  /**
   * For viewType === 'calendar': the schema ID of the date property used to
   * place entries on calendar cells.  Undefined means "not configured yet".
   */
  calendarDateSchemaId?: string
  /**
   * Per-entry chip color overrides for calendar / agenda views.
   * Maps entry ID → color key (see calendarColors.ts).
   */
  calendarChipColors?: Record<string, string>
  /**
   * Calendar rendering variant.
   * 'standard' (default) renders a monthly grid; 'agenda' renders a list.
   */
  calendarSubtype?: 'standard' | 'agenda'
  /**
   * Time-range granularity for the standard calendar grid.
   * 'month' (default) renders 6 week rows; 'week' renders a single week row;
   * 'day' renders a list view for a single day.
   * Only relevant when calendarSubtype === 'standard' (or absent).
   */
  calendarGranularity?: 'month' | 'week' | 'day'
  /**
   * For viewType === 'table': the schema ID to group rows by.
   * Undefined (or empty string) means no grouping (flat list).
   * The grouping is performed client-side from the already filtered/sorted
   * entries returned by the server query.
   */
  groupBySchemaId?: string
  /**
   * Per-column aggregation functions shown in the group footer rows.
   * Maps column key ('__name__' or schema ID) → AggregationType.
   * Only rendered when groupBySchemaId is set.
   * Persisted so the user's aggregation choices survive page reloads.
   */
  columnAggregations?: Record<string, AggregationType>
  /**
   * Column keys where text line-wrapping is enabled.
   * Absent or empty means all columns default to nowrap (single-line ellipsis).
   * Toggle is controlled per-column via the column-header wrap button.
   */
  wrapColumns?: string[]
  /**
   * For viewType === 'table': keep the column header row pinned to the top of
   * the scroll area while scrolling vertically.
   * Absent is treated as true (sticky); set to false to restore the legacy
   * behaviour where the header scrolls away with the content.
   */
  stickyHeader?: boolean
  /**
   * For viewType === 'table': number of leftmost columns (counted from the
   * Name column) that stay pinned while scrolling horizontally.
   * Range 0..MAX_FROZEN_COLUMNS; 0 (default) freezes nothing. The drag-handle
   * column is pinned alongside the data columns whenever this is >= 1.
   */
  frozenColumns?: number
  /**
   * Free-text description of this view, shown between the toolbar and the
   * view content and carried into the CSV / XLSX / PDF exports. Line breaks
   * are kept. Absent (never an empty string) when the view has none; see
   * ``normalizeViewDescription``.
   */
  description?: string
}

// ── Sticky-header / frozen-column helpers ─────────────────────────────────────

/** Maximum number of leftmost columns that can be frozen in a table view. */
export const MAX_FROZEN_COLUMNS = 3

/**
 * Clamp a raw frozen-column count into the valid 0..MAX_FROZEN_COLUMNS range.
 * Non-numeric or missing input resolves to 0.
 */
export function clampFrozenColumns(n: number | undefined | null): number {
  if (n == null || Number.isNaN(n)) return 0
  return Math.max(0, Math.min(MAX_FROZEN_COLUMNS, Math.floor(n)))
}

/**
 * Whether the column header should stay pinned while scrolling vertically.
 * Defaults to true when the field is absent (backward-compatible).
 */
export function isStickyHeaderEnabled(
  view: Pick<DatabaseView, 'stickyHeader'> | null | undefined,
): boolean {
  return (view?.stickyHeader ?? true) !== false
}

/**
 * Normalise a view description for storage: surrounding whitespace is
 * removed, inner line breaks are kept, and Windows line endings become plain
 * newlines. Returns ``''`` for a description that is empty or whitespace only,
 * which callers store as an absent field.
 */
export function normalizeViewDescription(raw: string | null | undefined): string {
  return (raw ?? '').replace(/\r\n?/g, '\n').trim()
}

// ── Query types (server-side filter / sort / pagination) ─────────────────────

/**
 * A single filter condition sent to POST /entries/query.
 * Field names use camelCase here; the store serialises to snake_case for
 * the API.
 */
export interface EntryQueryFilter {
  schemaId: string
  operator: FilterOperator
  value: string
  dateMode?: DateFilterMode
  dateOffset?: number
  /** For formula columns: the inferred result type ('text'|'number'|'boolean'|'date'). */
  formulaResultType?: string
  /** Upper bound for the 'between' operator (inclusive). */
  value2?: string
}

/** A single sort column sent to POST /entries/query. */
export interface EntryQuerySort {
  schemaId: string
  direction: 'asc' | 'desc'
}

export interface EntryQueryFilterGroup {
  conjunction: 'and' | 'or'
  filters: EntryQueryFilter[]
}

/** Full request body for POST /entries/query. */
export interface EntryQueryRequest {
  filter_groups: EntryQueryFilterGroup[]
  sorts: EntryQuerySort[]
  limit?: number
  offset?: number
}

/** Response from POST /entries/query. */
export interface EntryQueryResponse {
  entries: DatabaseEntry[]
  total: number
}

/**
 * Resolved relations carried by a view page or a single entry.
 *
 * ``relations`` maps every linked entry id that resolved to its descriptor;
 * an id the entries hold but the map lacks is gone or not readable.
 * ``key_values`` maps a key property id to the values linked entries hold for
 * it, by entry id.
 */
export interface RelationResolution {
  relations: Record<string, RelationTitle>
  key_values: Record<string, Record<string, Record<string, unknown> | null>>
}

/** Response of POST /views/{id}/entries. */
export interface ViewPageResponse extends RelationResolution {
  entries: DatabaseEntry[]
  total: number
  offset: number
}

/** Response of GET /entries/{id}. */
export interface EntryDetailResponse extends RelationResolution {
  entry: DatabaseEntry
}

/** Which part of a view to load; see ``loadViewPage``. */
export interface ViewPageRequest {
  offset: number
  /** Null loads every entry; views on a time axis are never paged anyway. */
  limit: number | null
  search: string
}

/** Property types whose values link to entries. */
const RELATION_TYPES = new Set(['relation', 'parent_item', 'sub_item'])

/**
 * Every entry id a relation value links to, across all timeline periods.
 * Mirrors ``linked_ids`` in the backend's resolution module.
 */
function linkedIds(value: Record<string, unknown> | null | undefined): string[] {
  if (!value || typeof value !== 'object') return []
  if ('_timeline' in value) {
    const timeline = value._timeline
    if (!timeline || typeof timeline !== 'object') return []
    const seen = new Set<string>()
    for (const slot of Object.values(timeline as Record<string, unknown>)) {
      const ids = (slot as Record<string, unknown> | null)?.related_ids
      if (Array.isArray(ids)) for (const id of ids) if (typeof id === 'string') seen.add(id)
    }
    return [...seen]
  }
  const ids = value.related_ids
  return Array.isArray(ids) ? ids.filter((id): id is string => typeof id === 'string') : []
}

/** Key property of a keyed relation, or null when keying is off. */
function keyPropertyOf(schema: PropertySchema): string | null {
  const keying = schema.config?.keying as Record<string, unknown> | undefined
  if (!keying || keying.enabled !== true) return null
  return typeof keying.key_property_id === 'string' && keying.key_property_id
    ? keying.key_property_id
    : null
}

/** Result of ``searchEntries``. */
export interface EntrySearchResult {
  /** Matching entries ordered by title, at most the requested limit. */
  entries: DatabaseEntry[]
  /** Number of matching entries in the whole database. */
  total: number
  /**
   * Whether an entry titled exactly like the query exists (case-insensitive),
   * determined across the whole database, not just the returned page. Always
   * false for an empty query.
   */
  exactMatch: boolean
}

/** One view's loaded entries, held under the key its caller chose. */
interface QueryResultSlot {
  databaseId: string
  entries: DatabaseEntry[]
}

/** One entry loaded on its own, for the side view or the entry page. */
interface EntryDetailSlot {
  databaseId: string
  entry: DatabaseEntry
}

// ── Store ─────────────────────────────────────────────────────────────────────

export const useDatabaseStore = defineStore('database', () => {
  /** Property schemas keyed by database block ID. */
  const schemas = ref<Record<string, PropertySchema[]>>({})

  /**
   * Complete entry lists (page-type blocks + values) keyed by database block
   * ID. Only ``fetchEntries`` writes here, so a list present here is always
   * the whole database.
   */
  const entries = ref<Record<string, DatabaseEntry[]>>({})

  /**
   * Loaded view entries keyed by the caller's result key. A view's result is
   * paginated and filtered, so it lives apart from ``entries`` and never
   * stands in for the whole database. Kept in the store rather than in the
   * view so that ``upsertValue`` and title updates reach the rows a view
   * renders.
   */
  const queryResults = ref<Record<string, QueryResultSlot>>({})

  /**
   * Single entries keyed by entry id, loaded by ``fetchEntry``. Kept apart
   * from the view results for the same reason, and reached by the same
   * in-place updates.
   */
  const entryDetails = ref<Record<string, EntryDetailSlot>>({})

  /** All database blocks available in the workspace (id + title). */
  const allDatabases = ref<DatabaseInfo[]>([])

  /**
   * Resolved relation-target descriptors keyed by entry id.
   *
   * Populated by ``resolveEntryTitles`` via POST /entries/resolve-titles and
   * kept deliberately separate from ``entries`` so a target database's
   * paginated display limit never drops a relation chip.
   */
  const relationTitles = ref<Record<string, RelationTitle>>({})

  /**
   * Entry ids for which a resolve round-trip has completed (whether the entry
   * was found or not). Lets relation cells distinguish "still loading" from
   * "trashed / not a target", so unresolved chips are hidden only after the
   * server has actually been asked.
   */
  const resolvedRelationIds = ref<Set<string>>(new Set())

  /**
   * Raw key values of relation targets, for keyed relations.
   *
   * Keyed by ``"<keyPropertyId>:<entryId>"`` rather than by entry id alone,
   * because two relations may key the same entry on different properties.
   * Kept separate from ``relationTitles`` for the same reason that cache exists
   * at all: the values are fetched on the display-limit-independent resolver
   * path, never read off the paginated ``entries`` cache.
   */
  const relationKeyValues = ref<Record<string, Record<string, unknown> | null>>({})

  /** Composite keys for which a key-value round-trip has completed. */
  const resolvedKeyValueIds = ref<Set<string>>(new Set())

  /** Cache key for the key-value maps above. */
  function keyValueCacheKey(keyPropertyId: string, entryId: string): string {
    return `${keyPropertyId}:${entryId}`
  }

  /**
   * In-flight schema fetches, so the many relation cells of one column do not
   * each issue their own request for the same target database's schemas.
   */
  const schemaFetches = new Map<string, Promise<PropertySchema[]>>()

  // ── All databases ─────────────────────────────────────────────────────────

  /**
   * Fetch the list of all database blocks from the server.
   *
   * Used to populate the target-database picker in the relation settings
   * panel. The result is stored in ``allDatabases``.
   */
  async function fetchAllDatabases(): Promise<DatabaseInfo[]> {
    const result = await apiClient.get<DatabaseInfo[]>('/api/databases')
    allDatabases.value = result
    return result
  }

  // ── Schemas ───────────────────────────────────────────────────────────────

  async function fetchSchemas(databaseId: string): Promise<PropertySchema[]> {
    const result = await apiClient.get<PropertySchema[]>(
      `/api/databases/${databaseId}/schemas`,
    )
    schemas.value[databaseId] = result
    return result
  }

  /**
   * Create a property. The backend hides it in every view of the database
   * except ``visible_in_view_id``, the view it was added from, if any; the
   * views on screen are reloaded to show that.
   */
  async function createSchema(
    databaseId: string,
    payload: {
      name: string
      type: string
      config?: Record<string, unknown> | null
      position?: number
      group?: string
      visible_in_view_id?: string
    },
  ): Promise<PropertySchema> {
    const schema = await apiClient.post<PropertySchema>(
      `/api/databases/${databaseId}/schemas`,
      payload,
    )
    await Promise.all([
      fetchSchemas(databaseId),
      useDatabaseViewsStore().refreshViews(databaseId),
    ])
    return schema
  }

  async function updateSchema(
    databaseId: string,
    schemaId: string,
    payload: {
      name?: string
      type?: string
      config?: Record<string, unknown> | null
      position?: number
      group?: string
    },
  ): Promise<PropertySchema> {
    const schema = await apiClient.patch<PropertySchema>(
      `/api/databases/${databaseId}/schemas/${schemaId}`,
      payload,
    )
    await fetchSchemas(databaseId)
    // A config change can rewrite entry values server-side (e.g. enabling
    // timeline wraps existing flat values into the _timeline format). The
    // embedded values are then stale, so re-fetch entries too when cached –
    // mirroring deleteSchema.
    if (entries.value[databaseId] !== undefined) {
      await fetchEntries(databaseId)
    }
    return schema
  }

  /**
   * Delete a property. The backend removes it from every view of the
   * database, so the views on screen are reloaded as well.
   */
  async function deleteSchema(databaseId: string, schemaId: string): Promise<void> {
    await apiClient.delete(`/api/databases/${databaseId}/schemas/${schemaId}`)
    await Promise.all([
      fetchSchemas(databaseId),
      useDatabaseViewsStore().refreshViews(databaseId),
    ])
    // Values embedded in entries are now stale – re-fetch entries too.
    if (entries.value[databaseId] !== undefined) {
      await fetchEntries(databaseId)
    }
  }

  /**
   * Return the schemas for *databaseId*, fetching them once if not cached.
   *
   * A keyed relation needs the target database's schemas to resolve its key
   * property, and every cell of that column would otherwise request them
   * independently on first render. Concurrent callers share one in-flight
   * promise.
   */
  async function ensureSchemas(databaseId: string): Promise<PropertySchema[]> {
    const cached = schemas.value[databaseId]
    if (cached !== undefined) return cached
    const inFlight = schemaFetches.get(databaseId)
    if (inFlight) return inFlight
    const request = fetchSchemas(databaseId).finally(() => {
      schemaFetches.delete(databaseId)
    })
    schemaFetches.set(databaseId, request)
    return request
  }

  /**
   * List the relation properties keyed on *schemaId*.
   *
   * Pre-flight for a destructive property edit: the returned relations are the
   * ones a delete would reset to vanilla, so the confirmation can name them.
   * A failure yields an empty list — the backend clears the references either
   * way, so a transient error must not block the delete.
   */
  async function listKeyReferences(
    databaseId: string,
    schemaId: string,
  ): Promise<KeyReference[]> {
    try {
      return await apiClient.get<KeyReference[]>(
        `/api/databases/${databaseId}/schemas/${schemaId}/key-references`,
      )
    } catch {
      return []
    }
  }

  /**
   * Number of active entries that use one option of a property: a select
   * option or a relation nuance. Pre-flight for removing the option, which
   * removes it from every entry.
   */
  async function optionUsage(
    databaseId: string,
    schemaId: string,
    optionId: string,
  ): Promise<number> {
    const result = await apiClient.get<{ entries: number }>(
      `/api/databases/${databaseId}/schemas/${schemaId}/options/${encodeURIComponent(optionId)}/usage`,
    )
    return result.entries
  }

  // ── Entries ───────────────────────────────────────────────────────────────

  async function fetchEntries(databaseId: string): Promise<DatabaseEntry[]> {
    const result = await apiClient.get<DatabaseEntry[]>(
      `/api/databases/${databaseId}/entries`,
    )
    entries.value[databaseId] = result
    return result
  }

  /** POST /entries/query with the request serialised to the API's snake_case. */
  async function _postQuery(
    databaseId: string,
    request: EntryQueryRequest,
  ): Promise<EntryQueryResponse> {
    const body = {
      filter_groups: request.filter_groups.map((g) => ({
        conjunction: g.conjunction,
        filters: g.filters.map((f) => ({
          schema_id: f.schemaId,
          operator: f.operator,
          value: f.value,
          date_mode: f.dateMode,
          date_offset: f.dateOffset,
          formula_result_type: f.formulaResultType,
          value2: f.value2,
        })),
      })),
      sorts: request.sorts.map((s) => ({
        schema_id: s.schemaId,
        direction: s.direction,
      })),
      limit: request.limit ?? 10_000,
      offset: request.offset ?? 0,
    }
    return apiClient.post<EntryQueryResponse>(
      `/api/databases/${databaseId}/entries/query`,
      body,
    )
  }

  /** Latest request per result key; see ``loadViewPage``. */
  const _pageGenerations = new Map<string, number>()

  /**
   * Load part of a view: its entries, filtered, sorted and paginated by the
   * backend according to the view, and narrowed by *request.search*.
   *
   * The entries are kept under *resultKey* in ``queryResults``. With
   * *append*, they are added below what that key already holds, which is
   * how loading more fetches only the next batch; otherwise they replace it.
   * The returned ``entries`` are that stored list, so in-place updates from
   * ``upsertValue`` and from WebSocket title changes reach the rows the
   * caller renders. A view passes a key unique to itself and releases it
   * with ``releaseQueryResult`` when it unmounts.
   *
   * The resolved relations of the page go into the chip caches before the
   * entries are stored, so relation cells render without asking for them.
   */
  async function loadViewPage(
    databaseId: string,
    viewId: string,
    request: ViewPageRequest,
    resultKey: string,
    append = false,
  ): Promise<{ entries: DatabaseEntry[]; total: number }> {
    // Requests for one key can overlap (a search typed quickly). Only the
    // latest may change the stored result, or an older response arriving late
    // would put stale rows back.
    const generation = (_pageGenerations.get(resultKey) ?? 0) + 1
    _pageGenerations.set(resultKey, generation)
    const page = await apiClient.post<ViewPageResponse>(
      `/api/databases/${databaseId}/views/${viewId}/entries`,
      { offset: request.offset, limit: request.limit, search: request.search },
    )
    const current = queryResults.value[resultKey]
    if (_pageGenerations.get(resultKey) !== generation) {
      return { entries: current?.entries ?? [], total: page.total }
    }
    ingestResolution(databaseId, page.entries, page)
    if (append && current && current.databaseId === databaseId) {
      // Entries may have moved between two batches; never show one twice.
      const known = new Set(current.entries.map((e) => e.id))
      current.entries.push(...page.entries.filter((e) => !known.has(e.id)))
    } else {
      queryResults.value[resultKey] = { databaseId, entries: page.entries }
    }
    return { entries: queryResults.value[resultKey].entries, total: page.total }
  }

  /** Drop the view query result held under *resultKey*. */
  function releaseQueryResult(resultKey: string): void {
    delete queryResults.value[resultKey]
    _pageGenerations.delete(resultKey)
  }

  /**
   * Load one entry with its values and resolved relations, and keep it in
   * ``entryDetails`` where in-place updates reach it. Returns the stored
   * entry.
   */
  async function fetchEntry(databaseId: string, entryId: string): Promise<DatabaseEntry> {
    const detail = await apiClient.get<EntryDetailResponse>(
      `/api/databases/${databaseId}/entries/${entryId}`,
    )
    ingestResolution(databaseId, [detail.entry], detail)
    entryDetails.value[entryId] = { databaseId, entry: detail.entry }
    return entryDetails.value[entryId].entry
  }

  /**
   * Put the resolved relations of *entries* into the chip caches.
   *
   * Every id the entries link to counts as resolved afterwards: the ones in
   * *resolution* with their descriptor, the others as gone, which hides their
   * chips exactly as an empty answer from ``resolveEntryTitles`` does. Keyed
   * relations get their key values the same way, null where the linked entry
   * stores none. Needs the schemas of *databaseId* to be loaded.
   */
  function ingestResolution(
    databaseId: string,
    entries: DatabaseEntry[],
    resolution: RelationResolution,
  ): void {
    const relationSchemas = getSchemas(databaseId).filter((s) => RELATION_TYPES.has(s.type))
    if (relationSchemas.length === 0) return
    for (const entry of entries) {
      for (const schema of relationSchemas) {
        const ids = linkedIds(entry.values[schema.id])
        if (ids.length === 0) continue
        for (const id of ids) {
          const descriptor = resolution.relations[id]
          if (descriptor) relationTitles.value[id] = descriptor
          else delete relationTitles.value[id]
          resolvedRelationIds.value.add(id)
        }
        const keyProperty = keyPropertyOf(schema)
        if (!keyProperty) continue
        const values = resolution.key_values[keyProperty] ?? {}
        for (const id of ids) {
          const cacheKey = keyValueCacheKey(keyProperty, id)
          relationKeyValues.value[cacheKey] = values[id] ?? null
          resolvedKeyValueIds.value.add(cacheKey)
        }
      }
    }
  }

  /**
   * Search a database's entries by title on the server.
   *
   * Used by relation pickers, which must reach every entry of the target
   * database rather than whatever page some view happens to have loaded. An
   * empty query lists the first *limit* entries by title.
   *
   * ``exactMatch`` decides whether a picker may offer to create an entry with
   * the query as its title. It is answered from the returned page when that
   * page holds every match, and by one more exact-title query otherwise, so an
   * existing entry past the page is never duplicated.
   *
   * Nothing is cached: search results are transient and belong to the picker.
   */
  async function searchEntries(
    databaseId: string,
    query: string,
    limit: number = ENTRY_SEARCH_LIMIT,
  ): Promise<EntrySearchResult> {
    const q = query.trim()
    const nameFilter = (operator: FilterOperator): EntryQueryFilterGroup[] =>
      q ? [{ conjunction: 'and', filters: [{ schemaId: NAME_COL_KEY, operator, value: q }] }] : []

    const result = await _postQuery(databaseId, {
      filter_groups: nameFilter('contains'),
      sorts: [{ schemaId: NAME_COL_KEY, direction: 'asc' }],
      limit,
    })
    if (!q) return { entries: result.entries, total: result.total, exactMatch: false }

    const wanted = q.toLowerCase()
    let exactMatch = result.entries.some(
      (e) => ((e.content?.title as string | undefined) ?? '').toLowerCase() === wanted,
    )
    if (!exactMatch && result.total > result.entries.length) {
      const exact = await _postQuery(databaseId, {
        filter_groups: nameFilter('eq'),
        sorts: [],
        limit: 1,
      })
      exactMatch = exact.total > 0
    }
    return { entries: result.entries, total: result.total, exactMatch }
  }

  /**
   * Resolve a set of relation-target entry ids to ``{id, title, database_id}``
   * descriptors and cache them in ``relationTitles``.
   *
   * Only ids that are neither already resolved nor currently being resolved
   * are requested, unless ``force`` is set (used after an inline edit to a
   * linked entry so the chip label refreshes). Ids that the server does not
   * return — trashed, foreign or unknown — are marked resolved but left out of
   * ``relationTitles``, which is how a relation cell knows to drop the chip.
   *
   * Failures are swallowed; the attempted-marker is rolled back for ids that
   * did not resolve so a transient error does not permanently blank a chip.
   */
  async function resolveEntryTitles(
    databaseId: string,
    ids: string[],
    force = false,
  ): Promise<void> {
    if (ids.length === 0) return
    const wanted = force
      ? ids
      : ids.filter(
          (id) => !(id in relationTitles.value) && !resolvedRelationIds.value.has(id),
        )
    if (wanted.length === 0) return
    // Mark up front so concurrent cells do not issue duplicate requests.
    for (const id of wanted) resolvedRelationIds.value.add(id)
    try {
      const result = await apiClient.post<RelationTitle[]>(
        `/api/databases/${databaseId}/entries/resolve-titles`,
        { ids: wanted },
      )
      const returned = new Set(result.map((r) => r.id))
      for (const r of result) {
        relationTitles.value[r.id] = r
      }
      // On a forced refresh, drop descriptors for ids that no longer resolve
      // (e.g. the entry was trashed since the last resolve).
      if (force) {
        for (const id of wanted) {
          if (!returned.has(id)) delete relationTitles.value[id]
        }
      }
    } catch {
      for (const id of wanted) {
        if (!(id in relationTitles.value)) resolvedRelationIds.value.delete(id)
      }
    }
  }

  /**
   * Resolve the key values of a keyed relation's linked entries and cache them
   * in ``relationKeyValues``.
   *
   * Shares the resolve-titles endpoint via its ``value_schema_id`` parameter,
   * which keeps the permission check and the active/non-template filter in one
   * place. It deliberately does *not* share ``resolveEntryTitles``: that
   * function skips ids whose titles are already cached, which would leave a
   * keyed cell without values for exactly the entries it has already rendered.
   *
   * Ids the server does not return are marked resolved with a null value, so
   * the sort treats them as empty instead of waiting forever. Failures are
   * swallowed and the attempted-marker rolled back, so a transient error does
   * not permanently pin the cell to seniority order.
   */
  async function resolveEntryKeyValues(
    databaseId: string,
    ids: string[],
    keyPropertyId: string,
    force = false,
  ): Promise<void> {
    if (ids.length === 0 || !keyPropertyId) return
    const wanted = force
      ? ids
      : ids.filter(
          (id) => !resolvedKeyValueIds.value.has(keyValueCacheKey(keyPropertyId, id)),
        )
    if (wanted.length === 0) return
    for (const id of wanted) {
      resolvedKeyValueIds.value.add(keyValueCacheKey(keyPropertyId, id))
    }
    try {
      const result = await apiClient.post<RelationTitle[]>(
        `/api/databases/${databaseId}/entries/resolve-titles`,
        { ids: wanted, value_schema_id: keyPropertyId },
      )
      const returned = new Set(result.map((r) => r.id))
      for (const r of result) {
        relationKeyValues.value[keyValueCacheKey(keyPropertyId, r.id)] = r.value ?? null
      }
      for (const id of wanted) {
        if (!returned.has(id)) {
          relationKeyValues.value[keyValueCacheKey(keyPropertyId, id)] = null
        }
      }
    } catch {
      for (const id of wanted) {
        const cacheKey = keyValueCacheKey(keyPropertyId, id)
        if (!(cacheKey in relationKeyValues.value)) {
          resolvedKeyValueIds.value.delete(cacheKey)
        }
      }
    }
  }

  /**
   * Create an entry. The caller reloads what it displays; nothing else is
   * fetched here, least of all the whole database.
   */
  async function createEntry(databaseId: string): Promise<DatabaseEntry> {
    return apiClient.post<DatabaseEntry>(`/api/databases/${databaseId}/entries`)
  }

  /**
   * Duplicate an existing entry, copying all writable property values,
   * content, icon and state in a single backend transaction.
   * The backend emits one ``database_entries_updated`` WS event so all
   * connected clients refresh at once. The caller reloads what it displays.
   */
  async function duplicateEntry(databaseId: string, entryId: string): Promise<DatabaseEntry> {
    return apiClient.post<DatabaseEntry>(
      `/api/databases/${databaseId}/entries/${entryId}/duplicate`,
    )
  }

  /**
   * Write a property value for one cell.
   *
   * Optimistically patches the local entry so the UI reflects the change
   * immediately without waiting for a round-trip re-fetch. For relation
   * properties with bilateral sync the mirror update is handled server-side;
   * no client-side coordination is required.
   */
  async function upsertValue(
    databaseId: string,
    entryId: string,
    schemaId: string,
    value: Record<string, unknown> | null,
  ): Promise<void> {
    // Optimistic local update so the cell does not flicker back on blur.
    // Reaches the full entry list as well as every view result of this
    // database, and entry_template blocks below.
    _forEachCachedEntry(entryId, databaseId, (row) => {
      row.values[schemaId] = value
    })

    // Also update the template store optimistically so the property picker
    // in DatabaseTemplateEditor reflects changes immediately.
    const templateStore = useDatabaseTemplatesStore()
    const tmplList = templateStore.templates[databaseId]
    if (tmplList) {
      const tmpl = tmplList.find((t) => t.id === entryId)
      if (tmpl) {
        tmpl.values[schemaId] = value
      }
    }

    await apiClient.put(
      `/api/databases/${databaseId}/entries/${entryId}/values/${schemaId}`,
      { value },
    )
  }

  // ── In-place entry updates ────────────────────────────────────────────────

  /**
   * Call *fn* with every cached copy of *entryId*: in the full entry lists, in
   * every view result and among the single entries. Restricted to one database when *databaseId* is
   * given; WebSocket events do not say which database a block belongs to, so
   * they search all of them.
   */
  function _forEachCachedEntry(
    entryId: string,
    databaseId: string | null,
    fn: (row: DatabaseEntry) => void,
  ): void {
    const lists: DatabaseEntry[][] = []
    if (databaseId !== null) {
      const full = entries.value[databaseId]
      if (full) lists.push(full)
    } else {
      lists.push(...Object.values(entries.value))
    }
    for (const slot of Object.values(queryResults.value)) {
      if (databaseId === null || slot.databaseId === databaseId) lists.push(slot.entries)
    }
    for (const list of lists) {
      const row = list.find((e) => e.id === entryId)
      if (row) fn(row)
    }
    const detail = entryDetails.value[entryId]
    if (detail && (databaseId === null || detail.databaseId === databaseId)) fn(detail.entry)
  }

  /**
   * Database a trashed relation target belonged to, remembered so that its
   * chip can be resolved again when the entry is restored.
   */
  const trashedRelationDatabase = new Map<string, string>()

  /**
   * Apply a block WebSocket event to the entry caches and relation chips.
   *
   * * ``content_updated`` / ``reverted`` – the entry's content (its title) is
   *   copied into every cached row and into its relation-chip descriptor.
   *   This is what makes a rename show on chips in other views at once, on
   *   this client and on every other one.
   * * ``state_changed`` – trashing drops the chip; restoring resolves it again.
   * * ``purged`` – the chip is dropped for good.
   *
   * Values are not patched here: value changes arrive as
   * ``database_entries_updated``, which views answer by re-querying.
   */
  function _applyEntryEvent(payload: BlockEventPayload): void {
    const id = payload.block_id
    if (!id) return

    switch (payload.event_type) {
      case 'content_updated':
      case 'reverted': {
        const after = payload.after as { content?: Record<string, unknown> | null } | null
        if (!after || !('content' in after)) return
        const content = after.content ?? null
        _forEachCachedEntry(id, null, (row) => {
          row.content = content
        })
        const descriptor = relationTitles.value[id]
        if (descriptor) {
          const title = content?.title
          descriptor.title = typeof title === 'string' ? title : null
        }
        return
      }
      case 'state_changed': {
        const state = (payload.after as { state?: string } | null)?.state
        if (state === 'trash') {
          const descriptor = relationTitles.value[id]
          if (descriptor) {
            trashedRelationDatabase.set(id, descriptor.database_id)
            delete relationTitles.value[id]
          }
        } else if (state === 'active') {
          const databaseId = trashedRelationDatabase.get(id)
          if (databaseId) {
            trashedRelationDatabase.delete(id)
            resolveEntryTitles(databaseId, [id], true)
          }
        }
        return
      }
      case 'purged':
        trashedRelationDatabase.delete(id)
        delete relationTitles.value[id]
        return
    }
  }

  function _onWsBlockEvent(e: Event): void {
    _applyEntryEvent((e as CustomEvent<BlockEventPayload>).detail)
  }

  window.addEventListener(WS_BLOCK_EVENT, _onWsBlockEvent)
  onScopeDispose(() => {
    window.removeEventListener(WS_BLOCK_EVENT, _onWsBlockEvent)
  })

  // ── Helpers ───────────────────────────────────────────────────────────────

  /** Return the schemas for *databaseId*, or an empty array if not loaded. */
  function getSchemas(databaseId: string): PropertySchema[] {
    return schemas.value[databaseId] ?? []
  }

  /**
   * Return one entry of *databaseId* if any cache holds it: the single entry
   * loaded for it, a view that shows it, or the full list. Callers that need
   * it fresh call ``fetchEntry``.
   */
  function getEntry(databaseId: string, entryId: string): DatabaseEntry | null {
    const detail = entryDetails.value[entryId]
    if (detail && detail.databaseId === databaseId) return detail.entry
    return getEntries(databaseId).find((e) => e.id === entryId) ?? null
  }

  /**
   * Return the cached entries of *databaseId*, for looking up an entry that is
   * already on screen.
   *
   * This is the complete list once ``fetchEntries`` has loaded it. Until then
   * it is the union of the rows the open views of that database have loaded,
   * so a side panel opened from a table finds its entry without fetching the
   * whole database. It is therefore not a list of every entry; a caller that
   * needs every entry calls ``fetchEntries``, and one that needs to search
   * calls ``searchEntries``.
   */
  function getEntries(databaseId: string): DatabaseEntry[] {
    const full = entries.value[databaseId]
    if (full) return full
    const seen = new Set<string>()
    const union: DatabaseEntry[] = []
    for (const slot of Object.values(queryResults.value)) {
      if (slot.databaseId !== databaseId) continue
      for (const entry of slot.entries) {
        if (seen.has(entry.id)) continue
        seen.add(entry.id)
        union.push(entry)
      }
    }
    return union
  }

  /**
   * The custom label of a database's lead property ("Name"), or ``null`` when
   * none is set and the localized default applies. Stored as ``nameLabel`` in
   * the database block's content; read from the block store, so the database
   * block must be loaded there.
   */
  function getNameLabel(databaseId: string): string | null {
    const raw = useBlockStore().blocks[databaseId]?.content?.nameLabel
    return typeof raw === 'string' && raw.trim() ? raw.trim() : null
  }

  /**
   * Resolved title text for a related entry id, or ``null`` when the id has
   * not (yet) resolved to an active entry. Untitled entries also resolve to
   * ``null`` here (no title text), so callers should supply their own
   * "untitled" fallback.
   */
  function getRelationTitle(id: string): string | null {
    return relationTitles.value[id]?.title ?? null
  }

  /** True once a resolve round-trip has completed for *id* (found or not). */
  function isRelationResolved(id: string): boolean {
    return resolvedRelationIds.value.has(id)
  }

  /** True when *id* resolved to an active entry of its target database. */
  function hasRelationEntry(id: string): boolean {
    return id in relationTitles.value
  }

  /**
   * Raw key value cached for *entryId* under *keyPropertyId*, or ``null`` when
   * none is known yet or the entry stores none. The keyed cell treats null as
   * an empty key, which is also the correct reading while a resolve is still
   * in flight: the entry sorts into the empty group and moves once the value
   * arrives.
   */
  function getRelationKeyValue(
    keyPropertyId: string,
    entryId: string,
  ): Record<string, unknown> | null {
    return relationKeyValues.value[keyValueCacheKey(keyPropertyId, entryId)] ?? null
  }

  return {
    // State
    schemas,
    entries,
    queryResults,
    entryDetails,
    allDatabases,
    relationTitles,
    relationKeyValues,

    // Database list
    fetchAllDatabases,

    // Schema operations
    fetchSchemas,
    ensureSchemas,
    createSchema,
    updateSchema,
    deleteSchema,
    getSchemas,
    listKeyReferences,
    optionUsage,

    // Entry operations
    fetchEntries,
    fetchEntry,
    loadViewPage,
    releaseQueryResult,
    searchEntries,
    createEntry,
    duplicateEntry,
    upsertValue,
    getEntry,
    getEntries,
    getNameLabel,
    ingestResolution,

    // WebSocket (exposed for tests)
    _applyEntryEvent,

    // Relation-chip title resolution
    resolveEntryTitles,
    getRelationTitle,
    isRelationResolved,
    hasRelationEntry,

    // Keyed-relation key values
    resolveEntryKeyValues,
    getRelationKeyValue,
  }
})

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
 * queryResults – keyed by a caller-chosen result key → the (possibly
 *                paginated) entries of one view query, see ``queryEntries``
 * allDatabases – flat list of DatabaseInfo (id + title), used by relation pickers
 *
 * The two entry caches are kept apart on purpose. A view shows a page of its
 * database; a relation picker or an export needs the whole database or a
 * search across it. When both shared one cache, a view's page silently became
 * everything the picker could see.
 *
 * View types (DatabaseView, ViewFilter, ViewSort) are defined here so that
 * DatabaseBlock and any future view-aware components share a single source of
 * truth.  Views themselves are persisted via blockStore.setPreference – the
 * database store does not own that persistence layer.
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
 * A single option in a select / multi-select property.
 * Stored in config.options[]. Backward-compatible with legacy string[] configs.
 */
export interface SelectOption {
  label: string
  color?: SelectColorKey
}

/**
 * Normalize a raw config option (may be a legacy string or a SelectOption object)
 * into a SelectOption. Safe to call on any value from config.options.
 */
export function normalizeSelectOption(opt: unknown): SelectOption {
  if (typeof opt === 'string') return { label: opt }
  const o = opt as SelectOption
  return { label: String(o.label ?? ''), color: o.color }
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
 * Example: (rank=General OR rank=Colonel) AND (faction=Empire AND active=true)
 * is expressed as two groups: first with 'or', second with 'and'.
 */
export interface FilterGroup {
  /** Stable UUID for v-for keying. */
  id: string
  conjunction: 'and' | 'or'
  filters: ViewFilter[]
}

/**
 * Persisted view descriptor stored as a JSON array under the block preference
 * key ``views``.  The active view ID is stored separately under ``active_view``.
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
  /**
   * Determines which renderer is used for this view.
   * Defaults to 'table' when absent (backward-compatible migration applied on load).
   */
  viewType: ViewType
  colOrder: string[]
  colWidths: Record<string, number>
  /**
   * Filter groups. Each group has its own conjunction; groups are ANDed
   * together. This supersedes the legacy `filters` flat list.
   */
  filterGroups: FilterGroup[]
  /**
   * @deprecated Legacy flat filter list. Kept for migration only.
   * On load, any non-empty `filters` is converted into a single AND group
   * appended to `filterGroups`, then cleared.
   */
  filters?: ViewFilter[]
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

/** One view's query result, held under the key its caller chose. */
interface QueryResultSlot {
  databaseId: string
  entries: DatabaseEntry[]
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
   * View query results keyed by the caller's result key. A view's result is
   * paginated and filtered, so it lives apart from ``entries`` and never
   * stands in for the whole database. Kept in the store rather than in the
   * view so that ``upsertValue`` and title updates reach the rows a view
   * renders.
   */
  const queryResults = ref<Record<string, QueryResultSlot>>({})

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

  async function createSchema(
    databaseId: string,
    payload: {
      name: string
      type: string
      config?: Record<string, unknown> | null
      position?: number
      group?: string
    },
  ): Promise<PropertySchema> {
    const schema = await apiClient.post<PropertySchema>(
      `/api/databases/${databaseId}/schemas`,
      payload,
    )
    await fetchSchemas(databaseId)
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

  async function deleteSchema(databaseId: string, schemaId: string): Promise<void> {
    await apiClient.delete(`/api/databases/${databaseId}/schemas/${schemaId}`)
    await fetchSchemas(databaseId)
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

  /** Latest request per result key; see ``queryEntries``. */
  const _queryGenerations = new Map<string, number>()

  /**
   * Run a server-side view query: filtered, sorted and paginated.
   *
   * When *resultKey* is given, the result is kept under that key in
   * ``queryResults`` and the returned ``entries`` are that stored list, so
   * in-place updates from ``upsertValue`` and from WebSocket title changes
   * reach the rows the caller renders. A view passes a key unique to itself
   * and releases it with ``releaseQueryResult`` when it unmounts.
   *
   * The result never touches ``entries``: a page of a database must not be
   * mistaken for the whole of it.
   */
  async function queryEntries(
    databaseId: string,
    request: EntryQueryRequest,
    resultKey?: string,
  ): Promise<EntryQueryResponse> {
    if (!resultKey) return _postQuery(databaseId, request)

    // Queries for one key can overlap (a search typed quickly). Only the
    // latest may replace the stored result, or an older response arriving
    // late would put stale rows back.
    const generation = (_queryGenerations.get(resultKey) ?? 0) + 1
    _queryGenerations.set(resultKey, generation)
    const result = await _postQuery(databaseId, request)
    if (_queryGenerations.get(resultKey) !== generation) return result
    queryResults.value[resultKey] = { databaseId, entries: result.entries }
    return { entries: queryResults.value[resultKey].entries, total: result.total }
  }

  /** Drop the view query result held under *resultKey*. */
  function releaseQueryResult(resultKey: string): void {
    delete queryResults.value[resultKey]
    _queryGenerations.delete(resultKey)
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

  async function createEntry(databaseId: string): Promise<DatabaseEntry> {
    const entry = await apiClient.post<DatabaseEntry>(
      `/api/databases/${databaseId}/entries`,
    )
    await fetchEntries(databaseId)
    return entry
  }

  /**
   * Duplicate an existing entry, copying all writable property values,
   * content, icon and state in a single backend transaction.
   * The backend emits one ``database_entries_updated`` WS event so all
   * connected clients refresh at once.
   */
  async function duplicateEntry(databaseId: string, entryId: string): Promise<DatabaseEntry> {
    const entry = await apiClient.post<DatabaseEntry>(
      `/api/databases/${databaseId}/entries/${entryId}/duplicate`,
    )
    await fetchEntries(databaseId)
    return entry
  }

  /**
   * Idempotently create the five system-managed readonly property schemas for
   * a database. Safe to call on every mount; schemas that already exist are
   * skipped server-side. Re-fetches schemas after the call.
   */
  async function seedReadonlySchemas(databaseId: string): Promise<PropertySchema[]> {
    const result = await apiClient.post<PropertySchema[]>(
      `/api/databases/${databaseId}/seed-readonly-schemas`,
    )
    await fetchSchemas(databaseId)
    return result
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
   * Call *fn* with every cached copy of *entryId*: in the full entry lists and
   * in every view result. Restricted to one database when *databaseId* is
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

    // Entry operations
    fetchEntries,
    queryEntries,
    releaseQueryResult,
    searchEntries,
    createEntry,
    duplicateEntry,
    seedReadonlySchemas,
    upsertValue,
    getEntries,
    getNameLabel,

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

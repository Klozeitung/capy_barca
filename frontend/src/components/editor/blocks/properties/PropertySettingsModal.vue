<script setup lang="ts">
/**
 * PropertySettingsModal
 *
 * Full settings editor for an existing property schema. Opened by clicking
 * a column header in DatabaseBlock.
 *
 * Type-specific configuration panels
 * -----------------------------------
 * text          – name only
 * number        – name + format (plain | euro)
 * checkbox      – name only
 * date          – name + includeTime flag + hasEndDate flag
 * select        – name + mode (single | multiple) + options list
 * relation      – name + target database + direction (unilateral | bilateral)
 *                 + mirror property name (bilateral only) + keying
 * file          – name only
 * email         – name only
 * phone         – name only
 * url           – name only
 * id            – name + prefix config
 * created_by    – name only (system hint)
 * created_time  – name only (system hint)
 * parent_item – name only (sub-item pair system hint)
 * sub_item     – name only (sub-item pair system hint, backend-managed)
 *
 * All types additionally expose an optional free-text *description* (stored at
 * config.description), surfaced as a column-header tooltip and embedded in the
 * CSV / PDF exports.
 *
 * Config JSONB shapes per type
 * ----------------------------
 * number:   { format: 'plain' | 'euro' }
 * date:     { includeTime: boolean, hasEndDate: boolean }
 * select:   { mode: 'single' | 'multiple', options: string[] }
 * relation: { target_database_id: string, direction: 'unilateral' | 'bilateral',
 *             mirror_property_name: string | null,
 *             keying: { enabled, key_property_id, key_order, key_empty_first } }
 *
 * Relation mode matrix
 * --------------------
 * A relation is exactly one of vanilla | timelined | nuanced |
 * timelined + nuanced | keyed. The controls below enforce that locally and the
 * backend enforces it authoritatively; the modal only makes the reason visible.
 * Because timeline and nuance are edge data and therefore shared by both sides
 * of a bilateral relation, a keyed mirror also locks them out on this side.
 * id:       { prefix: string, next_id: number }  (next_id managed by backend)
 */
import { ref, computed, watch, onMounted, nextTick } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { useDatabaseStore, type PropertySchema, type SelectOption, normalizeSelectOption, optionColorStyle, SELECT_OPTION_COLORS } from '@/stores/database'
import { getSchemaIcon } from '@/stores/propertyTypes'
import { isKeyableProperty } from './cells/cellUtils'

// ── Props / emits ─────────────────────────────────────────────────────────────

const props = defineProps<{
  schema: PropertySchema
  databaseId: string
}>()

const emit = defineEmits<{
  (e: 'close'): void
}>()

// ── Dependencies ──────────────────────────────────────────────────────────────

const { t } = useI18n()
const dbStore = useDatabaseStore()

// ── Draft state initialised from schema ───────────────────────────────────────

const nameEl = ref<HTMLInputElement | null>(null)
const name = ref(props.schema.name)
const nameError = ref('')
const isSaving = ref(false)

// description (all types) — free text, shown as the column-header tooltip and
// embedded in CSV / PDF exports. Persisted at config.description.
const description = ref<string>(
  (props.schema.config?.description as string | undefined) ?? '',
)

// Group is set in the side panel via drag and drop, not here.

// number
const numberFormat = ref<'plain' | 'euro'>(
  (props.schema.config?.format as 'plain' | 'euro' | undefined) ?? 'plain',
)

// date
const dateIncludeTime = ref<boolean>(
  (props.schema.config?.includeTime as boolean | undefined) ?? false,
)
const dateHasEndDate = ref<boolean>(
  (props.schema.config?.hasEndDate as boolean | undefined) ?? false,
)
// New date properties default to the global user preference; existing
// properties keep whatever explicit token they were saved with.
const dateFormat = ref<string>(
  (props.schema.config?.dateFormat as string | undefined) ?? 'global',
)

const DATE_FORMATS = [
  'DD.MM.YYYY',
  'MM.DD.YYYY',
  'YYYY-MM-DD',
  'YYYY-DD-MM',
] as const

// select — options stored as SelectOption[]; normalises legacy string[] transparently
const selectMode = ref<'single' | 'multiple'>(
  (props.schema.config?.mode as 'single' | 'multiple' | undefined) ?? 'single',
)
const selectOptions = ref<SelectOption[]>(
  ((props.schema.config?.options as (string | SelectOption)[] | undefined) ?? []).map(normalizeSelectOption),
)
const newOption = ref('')

// relation
const targetDatabaseId = ref<string>(
  (props.schema.config?.target_database_id as string | undefined) ?? props.databaseId,
)
const relationDirection = ref<'unilateral' | 'bilateral' | 'bilateral_self'>(
  (props.schema.config?.direction as 'unilateral' | 'bilateral' | 'bilateral_self' | undefined) ?? 'unilateral',
)
const mirrorPropertyName = ref<string>(
  (props.schema.config?.mirror_property_name as string | undefined) ?? '',
)

// relation nuance (irreversible once enabled, mirroring the hasTimeline gate).
// Each schema stores its own affixes/orientation at the top level; for bilateral
// relations the synched side's framing is held under ``synced`` so the backend
// can propagate it to the mirror schema and the modal can show both sides.
type NuanceOrientationT = 'prepended' | 'appended'
const _nuanceCfg = (props.schema.config?.nuance as Record<string, unknown> | undefined) ?? {}
const _nuanceSynced = (_nuanceCfg.synced as Record<string, unknown> | undefined) ?? {}
const nuanceEnabled = ref<boolean>(_nuanceCfg.enabled === true)
const nuanceOrientation = ref<NuanceOrientationT>(_nuanceCfg.orientation === 'appended' ? 'appended' : 'prepended')
const nuanceAffix1 = ref<string>((_nuanceCfg.affix1 as string | undefined) ?? '')
const nuanceAffix2 = ref<string>((_nuanceCfg.affix2 as string | undefined) ?? '')
const nuanceSyncedOrientation = ref<NuanceOrientationT>(_nuanceSynced.orientation === 'appended' ? 'appended' : 'prepended')
const nuanceSyncedAffix1 = ref<string>((_nuanceSynced.affix1 as string | undefined) ?? '')
const nuanceSyncedAffix2 = ref<string>((_nuanceSynced.affix2 as string | undefined) ?? '')
const nuanceOptions = ref<SelectOption[]>(
  ((_nuanceCfg.options as (string | SelectOption)[] | undefined) ?? []).map(normalizeSelectOption),
)
const newNuanceOption = ref('')

const isBilateralRelation = computed(() => relationDirection.value === 'bilateral')
const nuanceLocked = computed(() => (props.schema.config?.nuance as Record<string, unknown> | undefined)?.enabled === true)

// ── Relation keying ───────────────────────────────────────────────────────────
//
// Keying nominates a property of the target database that serves at once as the
// sort key for the relation's linked entries and as the value rendered beside
// each of them. Unlike timeline and nuance it is a read-side pointer, not edge
// data, so it is fully reversible and each side of a bilateral relation
// configures it independently.

const _keyingCfg = (props.schema.config?.keying as Record<string, unknown> | undefined) ?? {}
const keyingEnabled = ref<boolean>(_keyingCfg.enabled === true)
const keyingPropertyId = ref<string>(
  typeof _keyingCfg.key_property_id === 'string' ? _keyingCfg.key_property_id : '',
)
const keyingOrder = ref<'asc' | 'desc'>(_keyingCfg.key_order === 'desc' ? 'desc' : 'asc')
const keyingEmptyFirst = ref<boolean>(_keyingCfg.key_empty_first === true)
const keyingError = ref('')

/** Schemas of the relation's target database, loaded on mount. */
const relationTargetSchemas = ref<PropertySchema[]>([])

/** Those of them that can serve as a key property. */
const keyableTargetSchemas = computed(() =>
  relationTargetSchemas.value.filter(isKeyableProperty),
)

/** True once the target schemas are loaded and the pointer is among them. */
const keyingPropertyResolves = computed(
  () =>
    keyingPropertyId.value !== '' &&
    keyableTargetSchemas.value.some(s => s.id === keyingPropertyId.value),
)

/**
 * A pointer that survived the deletion or retyping of its target.
 *
 * Disabled keying keeps its pointer so re-enabling is one click, which means
 * the pointer can go stale while dormant. Surfacing it here is what stops the
 * modal from silently re-arming a dead selection.
 */
const keyingPropertyDangling = computed(
  () =>
    keyingPropertyId.value !== '' &&
    relationTargetSchemas.value.length > 0 &&
    !keyingPropertyResolves.value,
)

/**
 * The opposite side of a bilateral relation, resolved by mirror property name.
 * Null for unilateral and bilateral_self relations, which have no distinct
 * counterpart to lock against.
 */
const mirrorSchema = computed<PropertySchema | null>(() => {
  if (relationDirection.value !== 'bilateral') return null
  const mirrorName = mirrorPropertyName.value.trim() || props.schema.name
  return (
    relationTargetSchemas.value.find(
      s => s.type === 'relation' && s.name === mirrorName,
    ) ?? null
  )
})

const mirrorIsKeyed = computed(
  () =>
    (mirrorSchema.value?.config?.keying as Record<string, unknown> | undefined)
      ?.enabled === true,
)

const mirrorIsTimelinedOrNuanced = computed(() => {
  const config = mirrorSchema.value?.config
  if (!config) return false
  return (
    config.hasTimeline === true ||
    (config.nuance as Record<string, unknown> | undefined)?.enabled === true
  )
})

/** Why keying cannot be switched on right now, or '' when it can. */
const keyingBlockedReason = computed<string>(() => {
  if (hasTimeline.value) return t('db.settings.keyingBlockedByTimeline')
  if (nuanceEnabled.value) return t('db.settings.keyingBlockedByNuance')
  if (mirrorIsTimelinedOrNuanced.value) return t('db.settings.keyingBlockedByMirror')
  return ''
})

/** Why timeline and nuance cannot be switched on right now, or ''. */
const modeBlockedByKeyingReason = computed<string>(() => {
  if (keyingEnabled.value) return t('db.settings.keyingBlocksOthers')
  if (mirrorIsKeyed.value) return t('db.settings.keyingMirrorKeyed')
  return ''
})

// Turning keying on clears a stale pointer so the picker opens empty rather
// than showing a selection that no longer resolves.
watch(keyingEnabled, (enabled) => {
  keyingError.value = ''
  if (enabled && keyingPropertyDangling.value) keyingPropertyId.value = ''
})

// id
const idPrefix = ref<string>(
  (props.schema.config?.prefix as string | undefined) ?? '',
)

// timeline
const hasTimeline = ref<boolean>(
  (props.schema.config?.hasTimeline as boolean | undefined) ?? false,
)

// Computed: types that are ineligible for hasTimeline (computed / system / sub-item pair)
const isTimelineEligible = computed(() =>
  !['formula', 'rollup', 'id', 'created_by', 'created_time',
    'last_edited_by', 'last_edited_time', 'parent_item', 'sub_item'].includes(props.schema.type),
)

// ── formula ───────────────────────────────────────────────────────────────────

const formulaExpression = ref<string>(
  (props.schema.config?.expression as string | undefined) ?? '',
)

type ValidationState = 'idle' | 'validating' | 'valid' | 'error'
const formulaValidation = ref<ValidationState>('idle')
const formulaValidationError = ref<string>('')
const formulaPropNames = ref<string[]>([])

let validationTimer: ReturnType<typeof setTimeout> | null = null

async function validateFormula(): Promise<void> {
  if (!formulaExpression.value.trim()) {
    formulaValidation.value = 'idle'
    formulaValidationError.value = ''
    formulaPropNames.value = []
    return
  }
  formulaValidation.value = 'validating'
  try {
    const resp = await fetch(
      `/api/databases/${props.databaseId}/formulas/validate`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ expression: formulaExpression.value }),
      },
    )
    if (!resp.ok) {
      formulaValidation.value = 'error'
      formulaValidationError.value = t('errors.loadFailed')
      return
    }
    const data = await resp.json()
    if (data.valid) {
      formulaValidation.value = 'valid'
      formulaValidationError.value = ''
      formulaPropNames.value = data.prop_names ?? []
    } else {
      formulaValidation.value = 'error'
      formulaValidationError.value = data.error ?? t('db.settings.formulaStatusError')
      formulaPropNames.value = []
    }
  } catch {
    formulaValidation.value = 'error'
    formulaValidationError.value = t('errors.loadFailed')
  }
}

watch(formulaExpression, () => {
  if (validationTimer !== null) clearTimeout(validationTimer)
  formulaValidation.value = 'validating'
  validationTimer = setTimeout(validateFormula, 400)
})

const formulaTextareaEl = ref<HTMLTextAreaElement | null>(null)

function insertProp(propName: string): void {
  insertSnippet(`prop('${propName}')`)
}

function insertSnippet(snippet: string): void {
  const ta = formulaTextareaEl.value
  if (!ta) {
    formulaExpression.value = formulaExpression.value
      ? `${formulaExpression.value} ${snippet}`
      : snippet
    return
  }
  const start = ta.selectionStart ?? formulaExpression.value.length
  const end   = ta.selectionEnd   ?? start
  const before = formulaExpression.value.slice(0, start)
  const after  = formulaExpression.value.slice(end)
  const sep = before.length > 0 && !/\s$/.test(before) ? ' ' : ''
  formulaExpression.value = before + sep + snippet + after
  nextTick(() => {
    const pos = before.length + sep.length + snippet.length
    ta.focus()
    ta.setSelectionRange(pos, pos)
  })
}

// ── Formula help panel ────────────────────────────────────────────────────────

const formulaHelpOpen = ref(false)
const formulaHelpSearch = ref('')

interface FormulaHelpEntry {
  name: string
  signature: string
  description: string
  example: string
  category: 'logic' | 'math' | 'text' | 'list' | 'date' | 'operator'
  insert: string
}

const FORMULA_HELP: FormulaHelpEntry[] = [
  // ── Logic ──────────────────────────────────────────────────────────────────
  {
    name: 'if',
    signature: 'if(condition, thenValue, elseValue)',
    description: 'Returns thenValue when condition is true, otherwise elseValue.',
    example: "if(prop('Score') >= 90, 'A', 'B')",
    category: 'logic',
    insert: "if(, , )",
  },
  {
    name: 'ifs',
    signature: 'ifs(cond1, val1, cond2, val2, …, default?)',
    description: 'Returns the value paired with the first true condition. Optional final default when no condition matches.',
    example: "ifs(prop('Score') >= 90, 'A', prop('Score') >= 60, 'B', 'C')",
    category: 'logic',
    insert: "ifs(, , )",
  },
  {
    name: 'and',
    signature: 'and(a, b, …)',
    description: 'Returns true only when all arguments are truthy.',
    example: "and(prop('Done'), prop('Paid'))",
    category: 'logic',
    insert: 'and(, )',
  },
  {
    name: 'or',
    signature: 'or(a, b, …)',
    description: 'Returns true when at least one argument is truthy.',
    example: "or(prop('Draft'), prop('Review'))",
    category: 'logic',
    insert: 'or(, )',
  },
  {
    name: 'not',
    signature: 'not(value)',
    description: 'Inverts a boolean value.',
    example: "not(prop('Archived'))",
    category: 'logic',
    insert: 'not()',
  },
  // ── Math ───────────────────────────────────────────────────────────────────
  {
    name: 'abs',
    signature: 'abs(number)',
    description: 'Returns the absolute (non-negative) value of a number.',
    example: 'abs(-42)',
    category: 'math',
    insert: 'abs()',
  },
  {
    name: 'round',
    signature: 'round(number, digits?)',
    description: 'Rounds to the given number of decimal places (default 0).',
    example: "round(prop('Price'), 2)",
    category: 'math',
    insert: 'round(, 2)',
  },
  {
    name: 'ceil',
    signature: 'ceil(number)',
    description: 'Rounds up to the nearest integer.',
    example: 'ceil(3.1)',
    category: 'math',
    insert: 'ceil()',
  },
  {
    name: 'floor',
    signature: 'floor(number)',
    description: 'Rounds down to the nearest integer.',
    example: 'floor(3.9)',
    category: 'math',
    insert: 'floor()',
  },
  // ── Text ───────────────────────────────────────────────────────────────────
  {
    name: 'concat',
    signature: 'concat(a, b, …)',
    description: 'Joins multiple values into one string.',
    example: "concat('Hello ', prop('Name'))",
    category: 'text',
    insert: "concat(, )",
  },
  {
    name: 'len',
    signature: 'len(text)',
    description: 'Returns the number of characters in a string.',
    example: "len(prop('Notes'))",
    category: 'text',
    insert: 'len()',
  },
  {
    name: 'contains',
    signature: 'contains(text, substring)',
    description: 'Returns true if text contains the given substring. Case-sensitive.',
    example: "contains(prop('Sollkonto'), 'Giro')",
    category: 'text',
    insert: "contains(, '')",
  },
  {
    name: 'style',
    signature: 'style(value, hint, …)',
    description: 'Applies display hints to a value. Formatting: "b", "i", "u", "s", "c". Colors: "gray", "brown", "orange", "yellow", "green", "blue", "purple", "pink", "red". Add "_background" for background colors.',
    example: "if(prop('Balance') < 0, style(prop('Balance'), 'red'), prop('Balance'))",
    category: 'text',
    insert: "style(, 'red')",
  },
  {
    name: 'unstyle',
    signature: 'unstyle(value)',
    description: 'Removes all style hints from a styled value, returning the plain value.',
    example: "unstyle(prop('Styled Column'))",
    category: 'text',
    insert: 'unstyle()',
  },
  {
    name: 'equal',
    signature: 'equal(a, b)',
    description: 'Returns true if a equals b. Alias for the == operator (Notion compatibility).',
    example: "equal(prop('Status'), 'Done')",
    category: 'text',
    insert: 'equal(, )',
  },
  {
    name: 'divide',
    signature: 'divide(a, b)',
    description: 'Divides a by b. Alias for the / operator (Notion compatibility).',
    example: "divide(prop('Betrag'), 1.19)",
    category: 'math',
    insert: 'divide(, )',
  },
  // ── List ───────────────────────────────────────────────────────────────────
  {
    name: 'at',
    signature: 'at(list, index)',
    description: 'Returns the element at the given index in a list (e.g. a show_original rollup). Supports negative indexing: -1 is the last element. Returns null when the index is out of range.',
    example: "dateBetween(prop('Datum'), at(prop('Rollup Dates'), 0), 'years')",
    category: 'list',
    insert: "at(, 0)",
  },
  // ── Date & Time ────────────────────────────────────────────────────────────
  {
    name: 'now',
    signature: 'now()',
    description: 'Returns the current date and time (UTC).',
    example: "formatDate(now(), 'DD-MM-YYYY')",
    category: 'date',
    insert: 'now()',
  },
  {
    name: 'today',
    signature: 'today()',
    description: "Returns today's date at midnight UTC (no time component).",
    example: "dateBetween(prop('Deadline'), today(), 'days')",
    category: 'date',
    insert: 'today()',
  },
  {
    name: 'parseDate',
    signature: "parseDate(text)",
    description: 'Parses an ISO date string into a date value. Accepts YYYY-MM-DD or full ISO 8601.',
    example: "parseDate('2024-01-15')",
    category: 'date',
    insert: "parseDate('')",
  },
  {
    name: 'dateAdd',
    signature: "dateAdd(date, number, unit)",
    description: 'Adds the given amount to a date. Units: "years", "quarters", "months", "weeks", "days", "hours", "minutes".',
    example: "dateAdd(prop('Start'), 7, 'days')",
    category: 'date',
    insert: "dateAdd(, 1, 'days')",
  },
  {
    name: 'dateSubtract',
    signature: "dateSubtract(date, number, unit)",
    description: 'Subtracts the given amount from a date. Same units as dateAdd.',
    example: "dateSubtract(prop('Deadline'), 1, 'weeks')",
    category: 'date',
    insert: "dateSubtract(, 1, 'days')",
  },
  {
    name: 'dateBetween',
    signature: "dateBetween(date1, date2, unit)",
    description: 'Returns the truncated integer difference date1 − date2 in the given unit. Positive when date1 is later.',
    example: "dateBetween(now(), prop('Start'), 'days')",
    category: 'date',
    insert: "dateBetween(now(), , 'days')",
  },
  {
    name: 'formatDate',
    signature: "formatDate(date, format)",
    description: 'Formats a date using Moment.js-style tokens: YYYY MM DD HH mm ss MMMM MMM DDDD DDD A a.',
    example: "formatDate(now(), 'DD-MM-YYYY')",
    category: 'date',
    insert: "formatDate(, 'DD-MM-YYYY')",
  },
  {
    name: 'date / month / year',
    signature: 'date(d)  |  month(d)  |  year(d)',
    description: 'Extracts the day of month (1–31), month number (1–12), or year from a date.',
    example: "year(now())",
    category: 'date',
    insert: '',
  },
  {
    name: 'hour / minute',
    signature: 'hour(d)  |  minute(d)',
    description: 'Extracts the hour (0–23) or minute (0–59) from a date.',
    example: "hour(now())",
    category: 'date',
    insert: '',
  },
  {
    name: 'day / week',
    signature: 'day(d)  |  week(d)',
    description: 'Returns the ISO day of week (1 = Monday, 7 = Sunday) or ISO week number (1–53).',
    example: "day(now())",
    category: 'date',
    insert: '',
  },
  // ── Operators ──────────────────────────────────────────────────────────────
  {
    name: '+ / - / * / /',
    signature: 'a + b  |  a - b  |  a * b  |  a / b',
    description: 'Basic arithmetic. + also concatenates strings.',
    example: "prop('Price') * prop('Qty')",
    category: 'operator',
    insert: '',
  },
  {
    name: '% (modulo)',
    signature: 'a % b',
    description: 'Remainder of dividing a by b.',
    example: "prop('Items') % 2",
    category: 'operator',
    insert: '% ',
  },
  {
    name: '^ (power)',
    signature: 'a ^ b',
    description: 'Raises a to the power of b. Right-associative.',
    example: '2 ^ 10',
    category: 'operator',
    insert: '^ ',
  },
  {
    name: '== / != / < / <= / > / >=',
    signature: 'a == b  |  a != b  |  a < b  …',
    description: 'Comparison operators — return true or false.',
    example: "prop('Score') >= 60",
    category: 'operator',
    insert: '',
  },
  {
    name: 'and / or / not (keywords)',
    signature: 'a and b  |  a or b  |  not a',
    description: 'Logical operators usable as keywords between expressions.',
    example: "prop('Done') and not prop('Archived')",
    category: 'operator',
    insert: '',
  },
]

const CATEGORY_ORDER: FormulaHelpEntry['category'][] = ['logic', 'math', 'text', 'list', 'date', 'operator']

const filteredHelpEntries = computed<FormulaHelpEntry[]>(() => {
  const q = formulaHelpSearch.value.trim().toLowerCase()
  if (!q) return FORMULA_HELP
  return FORMULA_HELP.filter(
    e =>
      e.name.toLowerCase().includes(q) ||
      e.description.toLowerCase().includes(q) ||
      e.category.toLowerCase().includes(q),
  )
})

const filteredHelpByCategory = computed(() => {
  const map = new Map<FormulaHelpEntry['category'], FormulaHelpEntry[]>()
  for (const cat of CATEGORY_ORDER) map.set(cat, [])
  for (const entry of filteredHelpEntries.value) {
    map.get(entry.category)!.push(entry)
  }
  return map
})

// Schemas of this database (used for formula chip list and rollup relation picker)
const currentDbSchemas = ref<PropertySchema[]>([])

// ── rollup ────────────────────────────────────────────────────────────────────

const rollupRelationSchemaId = ref<string>(
  (props.schema.config?.relation_schema_id as string | undefined) ?? '',
)
const rollupSchemaId = ref<string>(
  (props.schema.config?.rollup_schema_id as string | undefined) ?? '',
)
const rollupFunction = ref<string>(
  (props.schema.config?.function as string | undefined) ?? 'count',
)
// Function-type badge (ERL / SUM …) shown in the cell — off by default.
const rollupShowTypeBadge = ref<boolean>(
  (props.schema.config?.show_type_badge as boolean | undefined) ?? false,
)
// Chip layout for relation / rollup cells — off by default.
const wrapContent = ref<boolean>(
  (props.schema.config?.wrapContent as boolean | undefined) ?? false,
)

const ROLLUP_FUNCTIONS = [
  // ── Count ──────────────────────────────────────────────────────────────────
  { value: 'count',          labelKey: 'db.settings.rollupFunctionCount' },
  { value: 'count_values',   labelKey: 'db.settings.rollupFunctionCountValues' },
  { value: 'count_empty',    labelKey: 'db.settings.rollupFunctionCountEmpty' },
  { value: 'count_not_empty',labelKey: 'db.settings.rollupFunctionCountNotEmpty' },
  { value: 'count_unique',   labelKey: 'db.settings.rollupFunctionCountUnique' },
  // ── Percent ────────────────────────────────────────────────────────────────
  { value: 'percent_empty',      labelKey: 'db.settings.rollupFunctionPercentEmpty' },
  { value: 'percent_not_empty',  labelKey: 'db.settings.rollupFunctionPercentNotEmpty' },
  { value: 'percent_checked',    labelKey: 'db.settings.rollupFunctionPercentChecked' },
  { value: 'percent_unchecked',  labelKey: 'db.settings.rollupFunctionPercentUnchecked' },
  { value: 'percent_per_option', labelKey: 'db.settings.rollupFunctionPercentPerOption' },
  // ── Checkbox ───────────────────────────────────────────────────────────────
  { value: 'checked', labelKey: 'db.settings.rollupFunctionChecked' },
  // ── Numeric ────────────────────────────────────────────────────────────────
  { value: 'sum',    labelKey: 'db.settings.rollupFunctionSum' },
  { value: 'avg',    labelKey: 'db.settings.rollupFunctionAvg' },
  { value: 'median', labelKey: 'db.settings.rollupFunctionMedian' },
  { value: 'min',    labelKey: 'db.settings.rollupFunctionMin' },
  { value: 'max',    labelKey: 'db.settings.rollupFunctionMax' },
  { value: 'range',  labelKey: 'db.settings.rollupFunctionRange' },
  // ── Raw values ─────────────────────────────────────────────────────────────
  { value: 'show_original', labelKey: 'db.settings.rollupFunctionShowOriginal' },
  { value: 'first_value',   labelKey: 'db.settings.rollupFunctionFirstValue' },
  { value: 'last_value',    labelKey: 'db.settings.rollupFunctionLastValue' },
  // ── Date ──────────────────────────────────────────────────────────────────
  { value: 'earliest_date', labelKey: 'db.settings.rollupFunctionEarliestDate' },
  { value: 'latest_date',   labelKey: 'db.settings.rollupFunctionLatestDate' },
  { value: 'date_range',    labelKey: 'db.settings.rollupFunctionDateRange' },
]

// Schemas from the target database the selected relation points to
const rollupTargetSchemas = ref<PropertySchema[]>([])
const rollupTargetLoading = ref(false)

const relationSchemas = computed(() =>
  currentDbSchemas.value.filter(s => s.type === 'relation'),
)

async function loadTargetSchemas(targetDbId: string): Promise<void> {
  if (!targetDbId) {
    rollupTargetSchemas.value = []
    return
  }
  rollupTargetLoading.value = true
  try {
    const resp = await fetch(`/api/databases/${targetDbId}/schemas`)
    if (resp.ok) {
      rollupTargetSchemas.value = await resp.json()
    }
  } catch {
    rollupTargetSchemas.value = []
  } finally {
    rollupTargetLoading.value = false
  }
}

watch(rollupRelationSchemaId, async (newId) => {
  rollupSchemaId.value = ''
  const relSchema = currentDbSchemas.value.find(s => s.id === newId)
  const targetDbId = (relSchema?.config?.target_database_id as string | undefined) ?? ''
  await loadTargetSchemas(targetDbId)
})

// ── Computed helpers ──────────────────────────────────────────────────────────

const isSystemReadonlyType = computed(() =>
  ['created_by', 'created_time', 'last_edited_by', 'last_edited_time', 'sub_item'].includes(props.schema.type),
)

// parent_item or sub_item — both are part of the linked hierarchy pair.
// Their config (partner_schema_id) is immutable; only the name may change.
const isSubItemPairType = computed(() =>
  ['parent_item', 'sub_item'].includes(props.schema.type),
)

// ── Bootstrap ─────────────────────────────────────────────────────────────────

onMounted(async () => {
  if (props.schema.type === 'relation') {
    await dbStore.fetchAllDatabases()
    // The keying picker lists properties of the target database, and the mode
    // lock needs the mirror schema's config. Both come from the same fetch.
    const relationTargetId =
      (props.schema.config?.target_database_id as string | undefined) ?? props.databaseId
    try {
      relationTargetSchemas.value = await dbStore.ensureSchemas(relationTargetId)
    } catch {
      relationTargetSchemas.value = []
    }
  }
  // Load schemas for formula chip list and rollup relation picker
  if (props.schema.type === 'formula' || props.schema.type === 'rollup') {
    try {
      const resp = await fetch(`/api/databases/${props.databaseId}/schemas`)
      if (resp.ok) currentDbSchemas.value = await resp.json()
    } catch { /* non-critical */ }
  }
  // For rollup: pre-load target DB schemas from the saved relation
  if (props.schema.type === 'rollup' && rollupRelationSchemaId.value) {
    const relSchema = currentDbSchemas.value.find(s => s.id === rollupRelationSchemaId.value)
    const targetDbId = (relSchema?.config?.target_database_id as string | undefined) ?? ''
    if (targetDbId) await loadTargetSchemas(targetDbId)
  }
  // Trigger initial formula validation if an expression is already saved
  if (props.schema.type === 'formula' && formulaExpression.value.trim()) {
    await validateFormula()
  }
  await nextTick()
  nameEl.value?.focus()
})

const allDatabases = computed(() => dbStore.allDatabases)

// ── Save ──────────────────────────────────────────────────────────────────────

async function save() {
  const trimmedName = name.value.trim()
  if (!trimmedName) {
    nameError.value = t('db.settings.errorNameRequired')
    return
  }
  nameError.value = ''
  isSaving.value = true

  // Keying must point at a property that exists, or it is silently inert.
  if (
    props.schema.type === 'relation' &&
    keyingEnabled.value &&
    !keyingPropertyResolves.value
  ) {
    keyingError.value = t('db.settings.keyingErrorProperty')
    isSaving.value = false
    return
  }
  keyingError.value = ''

  let config: Record<string, unknown> | null = props.schema.config

  switch (props.schema.type) {
    case 'number':
      config = { format: numberFormat.value, hasTimeline: hasTimeline.value }
      break
    case 'date':
      config = { includeTime: dateIncludeTime.value, hasEndDate: dateHasEndDate.value, dateFormat: dateFormat.value, hasTimeline: hasTimeline.value }
      break
    case 'select':
      config = { mode: selectMode.value, options: selectOptions.value, hasTimeline: hasTimeline.value }
      break
    case 'relation':
      // direction and target_database_id are immutable after creation.
      // Only mirror_property_name (bilateral) may change here.
      config = {
        target_database_id: props.schema.config?.target_database_id,
        direction: props.schema.config?.direction,
        mirror_property_name:
          (props.schema.config?.direction as string | undefined) === 'bilateral'
            ? mirrorPropertyName.value.trim() || null
            : null,
        hasTimeline: hasTimeline.value,
        wrapContent: wrapContent.value,
        nuance: nuanceEnabled.value
          ? {
              enabled: true,
              options: nuanceOptions.value,
              affix1: nuanceAffix1.value.slice(0, 20),
              affix2: nuanceAffix2.value.slice(0, 20),
              orientation: nuanceOrientation.value,
              ...(relationDirection.value === 'bilateral'
                ? {
                    synced: {
                      affix1: nuanceSyncedAffix1.value.slice(0, 20),
                      affix2: nuanceSyncedAffix2.value.slice(0, 20),
                      orientation: nuanceSyncedOrientation.value,
                    },
                  }
                : {}),
            }
          : { enabled: false },
        // A disabled block keeps its pointer so re-enabling is one click, but
        // only while the pointer still resolves — a dead one would reappear in
        // the picker as a blank selection.
        keying: keyingEnabled.value
          ? {
              enabled: true,
              key_property_id: keyingPropertyId.value,
              key_order: keyingOrder.value,
              key_empty_first: keyingEmptyFirst.value,
            }
          : keyingPropertyResolves.value
            ? {
                enabled: false,
                key_property_id: keyingPropertyId.value,
                key_order: keyingOrder.value,
                key_empty_first: keyingEmptyFirst.value,
              }
            : { enabled: false },
      }
      break
    case 'formula':
      config = { expression: formulaExpression.value.trim() }
      break
    case 'rollup':
      config = {
        relation_schema_id: rollupRelationSchemaId.value || null,
        rollup_schema_id:   rollupSchemaId.value || null,
        function:           rollupFunction.value,
        show_type_badge:    rollupShowTypeBadge.value,
        wrapContent:        wrapContent.value,
      }
      break
    case 'id': {
      // Preserve next_id when updating prefix – only the user-facing prefix changes.
      const existingConfig = props.schema.config ?? {}
      config = { ...existingConfig, prefix: idPrefix.value }
      break
    }
    case 'parent_item':
    case 'sub_item':
      // Config is locked (partner_schema_id must not change). Preserve as-is.
      config = props.schema.config
      break
    default:
      // text, checkbox, email, phone, url, file and other writable types
      config = { ...(props.schema.config ?? {}), hasTimeline: hasTimeline.value }
  }

  // Preserve a user-set column icon (config.icon). Several types above rebuild
  // their config object from scratch, which would otherwise drop the icon. The
  // modal never edits the icon, so it is always carried over from the existing
  // schema config when present.
  const existingIcon = props.schema.config?.icon as string | undefined
  if (existingIcon && config && typeof config === 'object') {
    config = { ...config, icon: existingIcon }
  }

  // Preserve the timeline display mode (config.timelineDisplayMode). It is set
  // via the TimelineEditor, never edited in this modal, but the per-type config
  // rebuilds above (number / date / select / relation) drop it. Carry it over
  // whenever the property still has a timeline so saving unrelated settings here
  // never resets the display mode back to its default.
  const existingDisplayMode = props.schema.config?.timelineDisplayMode as string | undefined
  if (
    existingDisplayMode &&
    config &&
    typeof config === 'object' &&
    (config as Record<string, unknown>).hasTimeline === true
  ) {
    config = { ...config, timelineDisplayMode: existingDisplayMode }
  }

  // Description (all types): the per-type config objects above are rebuilt from
  // scratch, so the free-text description must be (re)applied here regardless of
  // type. An empty description is stripped so we never persist empty strings.
  const trimmedDescription = description.value.trim()
  if (trimmedDescription) {
    config = { ...(config ?? {}), description: trimmedDescription }
  } else if (config && typeof config === 'object' && 'description' in config) {
    const { description: _omittedDescription, ...restConfig } = config as Record<string, unknown>
    config = restConfig
  }

  try {
    await dbStore.updateSchema(props.databaseId, props.schema.id, {
      name: trimmedName,
      config,
      // Group is set in the side panel via drag and drop, not here.
    })
    emit('close')
  } catch {
    nameError.value = t('db.settings.errorNameDuplicate')
  } finally {
    isSaving.value = false
  }
}

// ── Option management (select type) ──────────────────────────────────────────

function addOption() {
  const trimmed = newOption.value.trim()
  if (!trimmed || selectOptions.value.some(o => o.label === trimmed)) return
  selectOptions.value.push({ label: trimmed })
  newOption.value = ''
}

function removeOption(index: number) {
  selectOptions.value.splice(index, 1)
}

function moveOption(index: number, direction: -1 | 1) {
  const target = index + direction
  if (target < 0 || target >= selectOptions.value.length) return
  const arr = selectOptions.value
  ;[arr[index], arr[target]] = [arr[target], arr[index]]
}

// ── Nuance option management (recycles the select-option editor) ──────────────

function addNuanceOption() {
  const trimmed = newNuanceOption.value.trim()
  if (!trimmed || nuanceOptions.value.some(o => o.label === trimmed)) return
  nuanceOptions.value.push({ label: trimmed })
  newNuanceOption.value = ''
}

function removeNuanceOption(index: number) {
  nuanceOptions.value.splice(index, 1)
}

function moveNuanceOption(index: number, direction: -1 | 1) {
  const target = index + direction
  if (target < 0 || target >= nuanceOptions.value.length) return
  const arr = nuanceOptions.value
  ;[arr[index], arr[target]] = [arr[target], arr[index]]
}
</script>

<template>
  <div class="psm-backdrop" @mousedown.self="emit('close')">
    <div class="psm" role="dialog" :aria-label="t('db.settings.title')">

      <!-- ── Header ──────────────────────────────────────────────────────── -->
      <div class="psm__header">
        <Icon :icon="getSchemaIcon(schema)" width="15" height="15" class="psm__type-icon" />
        <span class="psm__header-title">{{ t('db.settings.title') }}</span>
        <button class="psm__close" @click="emit('close')" :aria-label="t('actions.cancel')">
          <Icon icon="mdi:close" width="15" height="15" />
        </button>
      </div>

      <!-- ── Body ────────────────────────────────────────────────────────── -->
      <div class="psm__body">

        <!-- Name (all types) -->
        <div class="psm__field">
          <label class="psm__label">{{ t('db.settings.nameLabel') }}</label>
          <input
            ref="nameEl"
            v-model="name"
            class="psm__input"
            :class="{ 'psm__input--error': nameError }"
            @keydown.enter.prevent="save"
            @keydown.escape.prevent="emit('close')"
          />
          <span v-if="nameError" class="psm__error">{{ nameError }}</span>
        </div>

        <!-- Description (all types) -->
        <div class="psm__field">
          <label class="psm__label">{{ t('db.settings.descriptionLabel') }}</label>
          <textarea
            v-model="description"
            class="psm__input psm__textarea"
            :placeholder="t('db.settings.descriptionPlaceholder')"
            rows="2"
            @keydown.escape.prevent="emit('close')"
          />
          <p class="psm__hint">{{ t('db.settings.descriptionHint') }}</p>
        </div>


        <!-- ── Number ──────────────────────────────────────────────────────── -->
        <template v-if="schema.type === 'number'">
          <div class="psm__field">
            <label class="psm__label">{{ t('db.settings.numberFormat') }}</label>
            <div class="psm__toggle-group">
              <button
                class="psm__toggle-btn"
                :class="{ 'psm__toggle-btn--active': numberFormat === 'plain' }"
                @click="numberFormat = 'plain'"
              >
                <Icon icon="mdi:numeric" width="15" height="15" />
                {{ t('db.settings.numberFormatPlain') }}
              </button>
              <button
                class="psm__toggle-btn"
                :class="{ 'psm__toggle-btn--active': numberFormat === 'euro' }"
                @click="numberFormat = 'euro'"
              >
                <Icon icon="mdi:currency-eur" width="15" height="15" />
                {{ t('db.settings.numberFormatEuro') }}
              </button>
            </div>
          </div>
        </template>

        <!-- ── Date ────────────────────────────────────────────────────────── -->
        <template v-else-if="schema.type === 'date'">
          <div class="psm__field">
            <label class="psm__label">{{ t('db.settings.dateFormat') }}</label>
            <select v-model="dateFormat" class="psm__native-select">
              <option value="global">{{ t('db.settings.dateFormatGlobal') }}</option>
              <option v-for="fmt in DATE_FORMATS" :key="fmt" :value="fmt">{{ fmt }}</option>
            </select>
          </div>
          <div class="psm__field">
            <label class="psm__label">{{ t('db.settings.dateOptions') }}</label>
            <div class="psm__check-group">
              <label class="psm__check-label">
                <input type="checkbox" v-model="dateIncludeTime" class="psm__checkbox" />
                {{ t('db.settings.dateIncludeTime') }}
              </label>
              <label class="psm__check-label">
                <input type="checkbox" v-model="dateHasEndDate" class="psm__checkbox" />
                {{ t('db.settings.dateHasEndDate') }}
              </label>
            </div>
          </div>
        </template>

        <!-- ── Select ──────────────────────────────────────────────────────── -->
        <template v-else-if="schema.type === 'select'">
          <div class="psm__field">
            <label class="psm__label">{{ t('db.settings.selectOptions') }}</label>
            <div class="psm__options-list">
              <div
                v-for="(opt, idx) in selectOptions"
                :key="idx"
                class="psm__option-row"
              >
                <Icon icon="mdi:drag-horizontal-variant" width="14" height="14" class="psm__option-drag" />
                <!-- Chip preview with color -->
                <span class="psm__option-chip" :style="optionColorStyle(opt.color)">{{ opt.label }}</span>
                <!-- Color dot picker -->
                <div class="psm__color-dots">
                  <button
                    v-for="c in SELECT_OPTION_COLORS"
                    :key="c.key"
                    class="psm__color-dot"
                    :class="{ 'psm__color-dot--active': (opt.color ?? 'default') === c.key }"
                    :style="optionColorStyle(c.key)"
                    :title="c.label"
                    type="button"
                    @click="opt.color = c.key"
                  />
                </div>
                <button
                  class="psm__option-move"
                  :disabled="idx === 0"
                  @click="moveOption(idx, -1)"
                >
                  <Icon icon="mdi:chevron-up" width="13" height="13" />
                </button>
                <button
                  class="psm__option-move"
                  :disabled="idx === selectOptions.length - 1"
                  @click="moveOption(idx, 1)"
                >
                  <Icon icon="mdi:chevron-down" width="13" height="13" />
                </button>
                <button class="psm__option-remove" @click="removeOption(idx)">
                  <Icon icon="mdi:close" width="13" height="13" />
                </button>
              </div>
              <div v-if="selectOptions.length === 0" class="psm__options-empty">
                {{ t('db.settings.selectOptionsEmpty') }}
              </div>
            </div>
            <div class="psm__option-add-row">
              <input
                v-model="newOption"
                class="psm__option-input"
                :placeholder="t('db.settings.selectOptionPlaceholder')"
                @keydown.enter.prevent="addOption"
              />
              <button class="psm__option-add-btn" @click="addOption">
                <Icon icon="mdi:plus" width="15" height="15" />
              </button>
            </div>
          </div>
        </template>

        <!-- ── Relation ─────────────────────────────────────────────────────── -->
        <!--
          Type, target database and direction are set once at creation time and
          are intentionally read-only here.  Only the property's own name and
          (for bilateral relations) the mirror property name may be changed.
        -->
        <template v-else-if="schema.type === 'relation'">
          <!-- Read-only: target database -->
          <div class="psm__field">
            <label class="psm__label">{{ t('db.settings.relationTarget') }}</label>
            <div class="psm__readonly-value">
              <Icon icon="mdi:table-large" width="14" height="14" class="psm__readonly-icon" />
              <span>
                {{
                  targetDatabaseId === databaseId
                    ? t('db.settings.relationSelf')
                    : (allDatabases.find(d => d.id === targetDatabaseId)?.title || t('main.untitled'))
                }}
              </span>
            </div>
          </div>

          <!-- Read-only: direction -->
          <div class="psm__field">
            <label class="psm__label">{{ t('db.settings.relationDirection') }}</label>
            <div class="psm__readonly-value">
              <Icon
                :icon="
                  relationDirection === 'bilateral' ? 'mdi:arrow-left-right'
                  : relationDirection === 'bilateral_self' ? 'mdi:arrow-u-left-top'
                  : 'mdi:arrow-right'
                "
                width="14" height="14" class="psm__readonly-icon"
              />
              <span>
                {{
                  relationDirection === 'bilateral'
                    ? t('db.settings.relationBilateral')
                    : relationDirection === 'bilateral_self'
                    ? t('db.settings.relationBilateralSelf')
                    : t('db.settings.relationUnilateral')
                }}
              </span>
            </div>
          </div>

          <!-- Editable: mirror property name (bilateral only, not bilateral_self) -->
          <div v-if="relationDirection === 'bilateral'" class="psm__field">
            <label class="psm__label">{{ t('db.settings.relationMirrorName') }}</label>
            <input
              v-model="mirrorPropertyName"
              class="psm__input"
              :placeholder="t('db.settings.relationMirrorPlaceholder')"
            />
            <p class="psm__hint">{{ t('db.settings.relationMirrorHint') }}</p>
          </div>

          <div class="psm__field">
            <label class="psm__check-label">
              <input type="checkbox" v-model="wrapContent" class="psm__checkbox" />
              {{ t('db.settings.wrapContent') }}
            </label>
            <p class="psm__hint">{{ t('db.settings.wrapContentHint') }}</p>
          </div>

          <!-- Keyed property: sort the linked entries by a target property -->
          <div class="psm__field">
            <label
              class="psm__check-label"
              :class="{ 'psm__check-label--disabled': !!keyingBlockedReason && !keyingEnabled }"
            >
              <input
                type="checkbox"
                v-model="keyingEnabled"
                class="psm__checkbox"
                :disabled="!!keyingBlockedReason && !keyingEnabled"
              />
              {{ t('db.settings.keyingEnable') }}
            </label>
            <p class="psm__hint">{{ t('db.settings.keyingHint') }}</p>
            <p
              v-if="!!keyingBlockedReason && !keyingEnabled"
              class="psm__hint psm__hint--warning"
            >
              {{ keyingBlockedReason }}
            </p>

            <template v-if="keyingEnabled">
              <label class="psm__label psm__nuance-sublabel">
                {{ t('db.settings.keyingProperty') }}
              </label>
              <select
                v-if="keyableTargetSchemas.length > 0"
                v-model="keyingPropertyId"
                class="psm__input"
              >
                <option value="">{{ t('db.settings.keyingPropertySelect') }}</option>
                <option v-for="s in keyableTargetSchemas" :key="s.id" :value="s.id">
                  {{ s.name }}
                </option>
              </select>
              <p v-else class="psm__hint psm__hint--warning">
                {{ t('db.settings.keyingNoKeyable') }}
              </p>
              <p v-if="keyingPropertyDangling" class="psm__hint psm__hint--warning">
                {{ t('db.settings.keyingDangling') }}
              </p>
              <p v-if="keyingError" class="psm__hint psm__hint--warning">
                {{ keyingError }}
              </p>

              <label class="psm__label psm__nuance-sublabel">
                {{ t('db.settings.keyingOrder') }}
              </label>
              <div class="psm__nuance-orient">
                <button
                  type="button"
                  class="psm__nuance-orient-btn"
                  :class="{ 'psm__nuance-orient-btn--active': keyingOrder === 'asc' }"
                  @click="keyingOrder = 'asc'"
                >{{ t('db.settings.keyingOrderAsc') }}</button>
                <button
                  type="button"
                  class="psm__nuance-orient-btn"
                  :class="{ 'psm__nuance-orient-btn--active': keyingOrder === 'desc' }"
                  @click="keyingOrder = 'desc'"
                >{{ t('db.settings.keyingOrderDesc') }}</button>
              </div>

              <label class="psm__label psm__nuance-sublabel">
                {{ t('db.settings.keyingEmpty') }}
              </label>
              <div class="psm__nuance-orient">
                <button
                  type="button"
                  class="psm__nuance-orient-btn"
                  :class="{ 'psm__nuance-orient-btn--active': keyingEmptyFirst }"
                  @click="keyingEmptyFirst = true"
                >{{ t('db.settings.keyingEmptyFirst') }}</button>
                <button
                  type="button"
                  class="psm__nuance-orient-btn"
                  :class="{ 'psm__nuance-orient-btn--active': !keyingEmptyFirst }"
                  @click="keyingEmptyFirst = false"
                >{{ t('db.settings.keyingEmptyLast') }}</button>
              </div>
            </template>
          </div>

          <!-- Nuanced property (irreversible once enabled) -->
          <div class="psm__field">
            <label class="psm__check-label" :class="{ 'psm__check-label--disabled': nuanceLocked || (!!modeBlockedByKeyingReason && !nuanceEnabled) }">
              <input
                type="checkbox"
                v-model="nuanceEnabled"
                class="psm__checkbox"
                :disabled="nuanceLocked || (!!modeBlockedByKeyingReason && !nuanceEnabled)"
              />
              {{ t('db.settings.nuanceEnable') }}
            </label>
            <p class="psm__hint">{{ t('db.settings.nuanceHint') }}</p>
            <p v-if="nuanceLocked" class="psm__hint psm__hint--warning">
              {{ t('db.settings.nuanceLocked') }}
            </p>
            <p
              v-else-if="!!modeBlockedByKeyingReason && !nuanceEnabled"
              class="psm__hint psm__hint--warning"
            >
              {{ modeBlockedByKeyingReason }}
            </p>

            <template v-if="nuanceEnabled">
              <label class="psm__label psm__nuance-sublabel">{{ t('db.settings.nuanceThisProperty') }}</label>
              <div class="psm__nuance-orient">
                <button
                  type="button"
                  class="psm__nuance-orient-btn"
                  :class="{ 'psm__nuance-orient-btn--active': nuanceOrientation === 'prepended' }"
                  @click="nuanceOrientation = 'prepended'"
                >{{ t('db.settings.nuancePrepended') }}</button>
                <button
                  type="button"
                  class="psm__nuance-orient-btn"
                  :class="{ 'psm__nuance-orient-btn--active': nuanceOrientation === 'appended' }"
                  @click="nuanceOrientation = 'appended'"
                >{{ t('db.settings.nuanceAppended') }}</button>
              </div>
              <div class="psm__nuance-row">
                <input v-model="nuanceAffix1" maxlength="20" class="psm__input psm__nuance-affix" :placeholder="t('db.settings.nuanceAffix')" />
                <span class="psm__nuance-word">{{ t('db.settings.nuanceWord') }}</span>
                <input v-model="nuanceAffix2" maxlength="20" class="psm__input psm__nuance-affix" :placeholder="t('db.settings.nuanceAffix')" />
              </div>

              <template v-if="isBilateralRelation">
                <label class="psm__label psm__nuance-sublabel">{{ t('db.settings.nuanceSynchedProperty') }}</label>
                <div class="psm__nuance-orient">
                  <button
                    type="button"
                    class="psm__nuance-orient-btn"
                    :class="{ 'psm__nuance-orient-btn--active': nuanceSyncedOrientation === 'prepended' }"
                    @click="nuanceSyncedOrientation = 'prepended'"
                  >{{ t('db.settings.nuancePrepended') }}</button>
                  <button
                    type="button"
                    class="psm__nuance-orient-btn"
                    :class="{ 'psm__nuance-orient-btn--active': nuanceSyncedOrientation === 'appended' }"
                    @click="nuanceSyncedOrientation = 'appended'"
                  >{{ t('db.settings.nuanceAppended') }}</button>
                </div>
                <div class="psm__nuance-row">
                  <input v-model="nuanceSyncedAffix1" maxlength="20" class="psm__input psm__nuance-affix" :placeholder="t('db.settings.nuanceAffix')" />
                  <span class="psm__nuance-word">{{ t('db.settings.nuanceWord') }}</span>
                  <input v-model="nuanceSyncedAffix2" maxlength="20" class="psm__input psm__nuance-affix" :placeholder="t('db.settings.nuanceAffix')" />
                </div>
              </template>

              <label class="psm__label psm__nuance-sublabel">{{ t('db.settings.nuanceOptions') }}</label>
              <div class="psm__options-list">
                <div
                  v-for="(opt, idx) in nuanceOptions"
                  :key="idx"
                  class="psm__option-row"
                >
                  <Icon icon="mdi:drag-horizontal-variant" width="14" height="14" class="psm__option-drag" />
                  <span class="psm__option-chip" :style="optionColorStyle(opt.color)">{{ opt.label }}</span>
                  <div class="psm__color-dots">
                    <button
                      v-for="c in SELECT_OPTION_COLORS"
                      :key="c.key"
                      class="psm__color-dot"
                      :class="{ 'psm__color-dot--active': (opt.color ?? 'default') === c.key }"
                      :style="optionColorStyle(c.key)"
                      :title="c.label"
                      type="button"
                      @click="opt.color = c.key"
                    />
                  </div>
                  <button class="psm__option-move" :disabled="idx === 0" @click="moveNuanceOption(idx, -1)">
                    <Icon icon="mdi:chevron-up" width="13" height="13" />
                  </button>
                  <button class="psm__option-move" :disabled="idx === nuanceOptions.length - 1" @click="moveNuanceOption(idx, 1)">
                    <Icon icon="mdi:chevron-down" width="13" height="13" />
                  </button>
                  <button class="psm__option-remove" @click="removeNuanceOption(idx)">
                    <Icon icon="mdi:close" width="13" height="13" />
                  </button>
                </div>
                <div v-if="nuanceOptions.length === 0" class="psm__options-empty">
                  {{ t('db.settings.selectOptionsEmpty') }}
                </div>
              </div>
              <div class="psm__option-add-row">
                <input
                  v-model="newNuanceOption"
                  class="psm__option-input"
                  :placeholder="t('db.settings.selectOptionPlaceholder')"
                  @keydown.enter.prevent="addNuanceOption"
                />
                <button class="psm__option-add-btn" @click="addNuanceOption">
                  <Icon icon="mdi:plus" width="15" height="15" />
                </button>
              </div>
            </template>
          </div>
        </template>

        <!-- ── ID ──────────────────────────────────────────────────────────── -->
        <template v-else-if="schema.type === 'id'">
          <div class="psm__field">
            <label class="psm__label">{{ t('db.settings.idPrefix') }}</label>
            <input
              v-model="idPrefix"
              class="psm__input"
              :placeholder="t('db.settings.idPrefixPlaceholder')"
            />
            <p class="psm__hint">{{ t('db.settings.idPrefixHint') }}</p>
          </div>
        </template>

        <!-- ── Formula ────────────────────────────────────────────────────────── -->
        <template v-else-if="schema.type === 'formula'">
          <div class="psm__field">
            <label class="psm__label">{{ t('db.settings.formulaExpression') }}</label>
            <div class="psm__formula-wrap">
              <textarea
                ref="formulaTextareaEl"
                v-model="formulaExpression"
                class="psm__formula-textarea"
                :placeholder="t('db.settings.formulaExpressionPlaceholder')"
                rows="4"
                spellcheck="false"
                autocorrect="off"
                autocapitalize="off"
              />
              <div
                class="psm__formula-status"
                :class="{
                  'psm__formula-status--valid':      formulaValidation === 'valid',
                  'psm__formula-status--validating':  formulaValidation === 'validating',
                  'psm__formula-status--error':       formulaValidation === 'error',
                }"
              >
                <template v-if="formulaValidation === 'valid'">
                  <Icon icon="mdi:check-circle-outline" width="13" height="13" />
                  {{ t('db.settings.formulaStatusValid') }}
                </template>
                <template v-else-if="formulaValidation === 'validating'">
                  <Icon icon="mdi:loading" width="13" height="13" class="psm__spin" />
                  {{ t('db.settings.formulaStatusValidating') }}
                </template>
                <template v-else-if="formulaValidation === 'error'">
                  <Icon icon="mdi:alert-circle-outline" width="13" height="13" />
                  {{ formulaValidationError }}
                </template>
              </div>
            </div>
            <p class="psm__hint">{{ t('db.settings.formulaExpressionHint') }}</p>
          </div>

          <!-- Properties chip strip -->
          <div class="psm__field">
            <label class="psm__label">{{ t('db.settings.formulaPropsLabel') }}</label>
            <div v-if="currentDbSchemas.filter(s => s.id !== schema.id).length === 0" class="psm__formula-empty">
              {{ t('db.settings.formulaPropsEmpty') }}
            </div>
            <div v-else class="psm__prop-chips">
              <button
                v-for="s in currentDbSchemas.filter(s => s.id !== schema.id)"
                :key="s.id"
                class="psm__prop-chip"
                type="button"
                @click="insertProp(s.name)"
              >
                <Icon :icon="getSchemaIcon(s)" width="12" height="12" />
                {{ s.name }}
              </button>
            </div>
          </div>

          <!-- Syntax help panel -->
          <div class="psm__field">
            <button
              class="psm__help-toggle"
              type="button"
              @click="formulaHelpOpen = !formulaHelpOpen"
            >
              <Icon
                :icon="formulaHelpOpen ? 'mdi:chevron-down' : 'mdi:chevron-right'"
                width="14" height="14"
              />
              {{ t('db.settings.formulaHelpToggle') }}
            </button>

            <div v-if="formulaHelpOpen" class="psm__help-panel">
              <!-- Search -->
              <div class="psm__help-search-wrap">
                <Icon icon="mdi:magnify" width="14" height="14" class="psm__help-search-icon" />
                <input
                  v-model="formulaHelpSearch"
                  class="psm__help-search"
                  :placeholder="t('db.settings.formulaHelpSearch')"
                  spellcheck="false"
                />
                <button
                  v-if="formulaHelpSearch"
                  class="psm__help-search-clear"
                  type="button"
                  @click="formulaHelpSearch = ''"
                >
                  <Icon icon="mdi:close" width="12" height="12" />
                </button>
              </div>

              <!-- No results -->
              <p v-if="filteredHelpEntries.length === 0" class="psm__help-empty">
                {{ t('db.settings.formulaHelpEmpty') }}
              </p>

              <!-- Categories -->
              <template v-for="cat in ['logic', 'math', 'text', 'list', 'date', 'operator']" :key="cat">
                <template v-if="(filteredHelpByCategory.get(cat as any) ?? []).length > 0">
                  <div class="psm__help-category">
                    {{ t(`db.settings.formulaHelpCat_${cat}`) }}
                  </div>
                  <div
                    v-for="entry in filteredHelpByCategory.get(cat as any)"
                    :key="entry.name"
                    class="psm__help-entry"
                    :class="{ 'psm__help-entry--no-insert': !entry.insert }"
                    @click="entry.insert ? insertSnippet(entry.insert) : undefined"
                  >
                    <div class="psm__help-entry__top">
                      <code class="psm__help-signature">{{ entry.signature }}</code>
                      <span v-if="entry.insert" class="psm__help-insert-hint">
                        <Icon icon="mdi:keyboard-return" width="11" height="11" />
                        {{ t('db.settings.formulaHelpInsert') }}
                      </span>
                    </div>
                    <p class="psm__help-desc">{{ entry.description }}</p>
                    <code class="psm__help-example">{{ entry.example }}</code>
                  </div>
                </template>
              </template>
            </div>
          </div>
        </template>

        <!-- ── Rollup ──────────────────────────────────────────────────────────── -->
        <template v-else-if="schema.type === 'rollup'">
          <div class="psm__field">
            <label class="psm__label">{{ t('db.settings.rollupRelation') }}</label>
            <select v-model="rollupRelationSchemaId" class="psm__native-select">
              <option value="">{{ t('db.settings.rollupRelationPlaceholder') }}</option>
              <option
                v-for="s in relationSchemas"
                :key="s.id"
                :value="s.id"
              >{{ s.name }}</option>
            </select>
          </div>

          <div class="psm__field">
            <label class="psm__label">{{ t('db.settings.rollupColumn') }}</label>
            <select
              v-model="rollupSchemaId"
              class="psm__native-select"
              :disabled="!rollupRelationSchemaId || rollupTargetLoading"
            >
              <option value="">
                {{ rollupTargetLoading
                  ? t('db.settings.rollupLoadingColumns')
                  : t('db.settings.rollupColumnPlaceholder') }}
              </option>
              <option
                v-for="s in rollupTargetSchemas.filter(s => s.type !== 'rollup')"
                :key="s.id"
                :value="s.id"
              >{{ s.name }}</option>
            </select>
          </div>

          <div class="psm__field">
            <label class="psm__label">{{ t('db.settings.rollupFunction') }}</label>
            <div class="psm__toggle-group psm__toggle-group--wrap">
              <button
                v-for="fn in ROLLUP_FUNCTIONS"
                :key="fn.value"
                class="psm__toggle-btn"
                :class="{ 'psm__toggle-btn--active': rollupFunction === fn.value }"
                type="button"
                @click="rollupFunction = fn.value"
              >
                {{ t(fn.labelKey) }}
              </button>
            </div>
            <p class="psm__hint">{{ t('db.settings.rollupHint') }}</p>
          </div>

          <div class="psm__field">
            <label class="psm__check-label">
              <input type="checkbox" v-model="rollupShowTypeBadge" class="psm__checkbox" />
              {{ t('db.settings.rollupShowTypeBadge') }}
            </label>
            <p class="psm__hint">{{ t('db.settings.rollupShowTypeBadgeHint') }}</p>
          </div>

          <div class="psm__field">
            <label class="psm__check-label">
              <input type="checkbox" v-model="wrapContent" class="psm__checkbox" />
              {{ t('db.settings.wrapContent') }}
            </label>
            <p class="psm__hint">{{ t('db.settings.wrapContentHint') }}</p>
          </div>
        </template>

        <!-- ── Sub-item pair (parent_item / sub_item) ──────────────────────── -->
        <template v-else-if="isSubItemPairType">
          <div class="psm__field">
            <p class="psm__hint psm__hint--system">
              <Icon icon="mdi:link-lock" width="13" height="13" />
              {{ t('db.settings.subItemPairHint') }}
            </p>
          </div>
        </template>

        <!-- ── System readonly (created_by, created_time, etc.) ─────────────── -->
        <template v-else-if="isSystemReadonlyType">
          <div class="psm__field">
            <p class="psm__hint psm__hint--system">
              <Icon icon="mdi:lock-outline" width="13" height="13" />
              {{ t('db.settings.systemTypeHint') }}
            </p>
          </div>
        </template>

        <!-- ── Timeline toggle (all writable types except formula/rollup) ──── -->
        <template v-if="isTimelineEligible">
          <div class="psm__field psm__field--divider">
            <label class="psm__label">{{ t('db.timeline.hasTimeline') }}</label>
            <label
              class="psm__check-label"
              :class="{ 'psm__check-label--disabled': !!schema.config?.hasTimeline || (!!modeBlockedByKeyingReason && !hasTimeline) }"
            >
              <input
                type="checkbox"
                v-model="hasTimeline"
                class="psm__checkbox"
                :disabled="!!schema.config?.hasTimeline || (!!modeBlockedByKeyingReason && !hasTimeline)"
              />
              {{ t('db.timeline.hasTimeline') }}
            </label>
            <p class="psm__hint">{{ t('db.timeline.hasTimelineHint') }}</p>
            <p v-if="schema.config?.hasTimeline" class="psm__hint psm__hint--warning">
              {{ t('db.timeline.disableBlocked') }}
            </p>
            <p
              v-else-if="!!modeBlockedByKeyingReason && !hasTimeline"
              class="psm__hint psm__hint--warning"
            >
              {{ modeBlockedByKeyingReason }}
            </p>
            <p v-else-if="hasTimeline" class="psm__hint psm__hint--warning">
              {{ t('db.timeline.migrateWarning') }}
            </p>
          </div>
        </template>

      </div>

      <!-- ── Footer ──────────────────────────────────────────────────────── -->
      <div class="psm__footer">
        <button class="psm__btn psm__btn--ghost" @click="emit('close')">
          {{ t('actions.cancel') }}
        </button>
        <button
          class="psm__btn psm__btn--primary"
          :disabled="isSaving"
          @click="save"
        >
          {{ t('actions.save') }}
        </button>
      </div>

    </div>
  </div>
</template>

<style scoped>
/* ── Backdrop / dialog ───────────────────────────────────────────────────── */
.psm-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 200;
}

.psm {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  width: min(420px, 92vw);
  max-height: 82vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.25);
}

/* ── Header ──────────────────────────────────────────────────────────────── */
.psm__header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 13px 16px;
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.psm__type-icon {
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.psm__header-title {
  flex: 1;
  font-size: 0.875rem;
  font-weight: 600;
}

.psm__close {
  background: transparent;
  border: none;
  cursor: pointer;
  color: var(--color-text-muted);
  display: flex;
  align-items: center;
  padding: 3px;
  border-radius: 4px;
  transition: color 0.15s, background 0.15s;
}

.psm__close:hover {
  color: var(--color-text);
  background: var(--color-hover);
}

/* ── Body ────────────────────────────────────────────────────────────────── */
.psm__body {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* ── Field ───────────────────────────────────────────────────────────────── */
.psm__field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.psm__field--divider {
  border-top: 1px solid var(--color-border);
  padding-top: 12px;
  margin-top: 4px;
}

.psm__label {
  font-size: 0.72rem;
  font-weight: 600;
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.psm__input {
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 4px;
  padding: 8px 10px;
  font-size: 0.875rem;
  color: var(--color-text);
  outline: none;
  transition: border-color 0.15s;
}

.psm__input:focus {
  border-color: var(--color-accent);
}

.psm__input--error {
  border-color: #e05555;
}

.psm__textarea {
  resize: vertical;
  min-height: 56px;
  font-family: inherit;
  line-height: 1.4;
}

.psm__error {
  font-size: 0.75rem;
  color: #e05555;
}

.psm__hint {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  margin: 0;
}

.psm__hint--warning {
  color: #c97000;
  background: rgba(201, 112, 0, 0.08);
  border-radius: 4px;
  padding: 6px 8px;
}

.psm__hint--system {
  display: flex;
  align-items: center;
  gap: 5px;
  background: var(--color-hover);
  border-radius: 5px;
  padding: 8px 10px;
}

/* ── Toggle group ────────────────────────────────────────────────────────── */
.psm__toggle-group {
  display: flex;
  gap: 6px;
}

.psm__toggle-btn {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 8px 10px;
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.8rem;
  color: var(--color-text-muted);
  transition: border-color 0.15s, color 0.15s, background 0.15s;
}

.psm__toggle-btn:hover {
  border-color: var(--color-accent);
  color: var(--color-text);
}

.psm__toggle-btn--active {
  border-color: var(--color-accent);
  background: var(--color-accent-subtle);
  color: var(--color-text);
}

/* ── Checkbox group (date options) ───────────────────────────────────────── */
.psm__check-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.psm__check-label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.875rem;
  color: var(--color-text);
  cursor: pointer;
  user-select: none;
}

.psm__check-label--disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.psm__checkbox {
  width: 15px;
  height: 15px;
  accent-color: var(--color-accent);
  cursor: pointer;
}

/* ── Native select (relation target) ─────────────────────────────────────── */
.psm__native-select {
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 4px;
  padding: 8px 10px;
  font-size: 0.875rem;
  color: var(--color-text);
  outline: none;
  cursor: pointer;
  transition: border-color 0.15s;
}

.psm__native-select:focus {
  border-color: var(--color-accent);
}

/* ── Options list (select type) ──────────────────────────────────────────── */
.psm__options-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 180px;
  overflow-y: auto;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  padding: 4px;
  background: var(--color-bg);
}

.psm__option-row {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 6px;
  border-radius: 3px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
}

.psm__option-drag {
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.psm__option-label {
  flex: 1;
  font-size: 0.875rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Chip preview for select option */
.psm__option-chip {
  border-radius: 3px;
  padding: 1px 7px;
  font-size: 0.75rem;
  border: 1px solid;
  white-space: nowrap;
  flex-shrink: 0;
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Color dot strip */
.psm__color-dots {
  display: flex;
  gap: 3px;
  flex-shrink: 0;
}

.psm__color-dot {
  width: 13px;
  height: 13px;
  border-radius: 50%;
  border: 1px solid transparent;
  cursor: pointer;
  padding: 0;
  flex-shrink: 0;
  transition: transform 0.1s, box-shadow 0.1s;
}

.psm__color-dot:hover {
  transform: scale(1.25);
}

.psm__color-dot--active {
  box-shadow: 0 0 0 2px var(--color-accent);
}

.psm__option-move,
.psm__option-remove {
  background: transparent;
  border: none;
  cursor: pointer;
  color: var(--color-text-muted);
  display: flex;
  align-items: center;
  padding: 2px;
  border-radius: 3px;
  flex-shrink: 0;
  transition: color 0.15s, background 0.15s;
}

.psm__option-move:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

.psm__option-move:not(:disabled):hover {
  color: var(--color-text);
  background: var(--color-hover);
}

.psm__option-remove:hover {
  color: #e05555;
  background: var(--color-hover);
}

.psm__options-empty {
  font-size: 0.8rem;
  color: var(--color-text-muted);
  text-align: center;
  padding: 8px;
  font-style: italic;
}

.psm__option-add-row {
  display: flex;
  gap: 6px;
}

.psm__option-input {
  flex: 1;
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 4px;
  padding: 6px 8px;
  font-size: 0.875rem;
  color: var(--color-text);
  outline: none;
  transition: border-color 0.15s;
}

.psm__option-input:focus {
  border-color: var(--color-accent);
}

.psm__option-add-btn {
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 4px;
  cursor: pointer;
  color: var(--color-text-muted);
  display: flex;
  align-items: center;
  padding: 6px 8px;
  transition: color 0.15s, border-color 0.15s;
}

.psm__option-add-btn:hover {
  color: var(--color-text);
  border-color: var(--color-accent);
}

/* ── Formula ─────────────────────────────────────────────────────────────── */
.psm__formula-wrap {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.psm__formula-textarea {
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 4px;
  padding: 8px 10px;
  font-size: 0.8rem;
  font-family: 'Fira Mono', 'Consolas', 'Courier New', monospace;
  color: var(--color-text);
  outline: none;
  resize: vertical;
  line-height: 1.5;
  transition: border-color 0.15s;
}

.psm__formula-textarea:focus {
  border-color: var(--color-accent);
}

.psm__formula-status {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 0.72rem;
  color: var(--color-text-muted);
  min-height: 18px;
}

.psm__formula-status--valid {
  color: #3dba76;
}

.psm__formula-status--error {
  color: #e05555;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.psm__formula-empty {
  font-size: 0.8rem;
  color: var(--color-text-muted);
  font-style: italic;
  padding: 4px 0;
}

.psm__prop-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.psm__prop-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 12px;
  font-size: 0.775rem;
  color: var(--color-text);
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
  white-space: nowrap;
}

.psm__prop-chip:hover {
  border-color: var(--color-accent);
  background: var(--color-accent-subtle);
}

/* spin animation for validating icon */
@keyframes psm-spin {
  to { transform: rotate(360deg); }
}
.psm__spin {
  animation: psm-spin 0.8s linear infinite;
}

/* ── Syntax help panel ───────────────────────────────────────────────────── */
.psm__help-toggle {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  background: transparent;
  border: none;
  cursor: pointer;
  color: var(--color-text-muted);
  font-size: 0.775rem;
  font-weight: 600;
  padding: 2px 0;
  transition: color 0.15s;
}

.psm__help-toggle:hover {
  color: var(--color-text);
}

.psm__help-panel {
  margin-top: 6px;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-bg);
  max-height: 340px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}

/* Search row */
.psm__help-search-wrap {
  position: sticky;
  top: 0;
  z-index: 1;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 10px;
  background: var(--color-bg);
  border-bottom: 1px solid var(--color-border);
}

.psm__help-search-icon {
  flex-shrink: 0;
  color: var(--color-text-muted);
}

.psm__help-search {
  flex: 1;
  background: transparent;
  border: none;
  outline: none;
  font-size: 0.8rem;
  color: var(--color-text);
  min-width: 0;
}

.psm__help-search::placeholder {
  color: var(--color-text-muted);
}

.psm__help-search-clear {
  background: transparent;
  border: none;
  cursor: pointer;
  color: var(--color-text-muted);
  display: flex;
  align-items: center;
  padding: 2px;
  border-radius: 3px;
  transition: color 0.12s;
}

.psm__help-search-clear:hover {
  color: var(--color-text);
}

/* Category header */
.psm__help-category {
  padding: 6px 10px 3px;
  font-size: 0.65rem;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--color-text-muted);
  background: var(--color-surface);
  position: sticky;
  top: 35px;
  z-index: 0;
  border-bottom: 1px solid var(--color-border);
}

/* Individual entry */
.psm__help-entry {
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 3px;
  cursor: pointer;
  border-bottom: 1px solid var(--color-border);
  transition: background 0.1s;
}

.psm__help-entry:last-child {
  border-bottom: none;
}

.psm__help-entry:hover {
  background: var(--color-hover);
}

.psm__help-entry--no-insert {
  cursor: default;
}

.psm__help-entry--no-insert:hover {
  background: transparent;
}

.psm__help-entry__top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.psm__help-signature {
  font-size: 0.775rem;
  font-family: 'Fira Mono', 'Consolas', 'Courier New', monospace;
  color: var(--color-text);
  white-space: pre;
  overflow: hidden;
  text-overflow: ellipsis;
}

.psm__help-insert-hint {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 0.65rem;
  color: var(--color-text-muted);
  opacity: 0;
  transition: opacity 0.12s;
}

.psm__help-entry:hover .psm__help-insert-hint {
  opacity: 1;
}

.psm__help-desc {
  font-size: 0.775rem;
  color: var(--color-text-muted);
  margin: 0;
  line-height: 1.4;
}

.psm__help-example {
  font-size: 0.72rem;
  font-family: 'Fira Mono', 'Consolas', 'Courier New', monospace;
  color: var(--color-accent);
  background: var(--color-accent-subtle);
  border-radius: 3px;
  padding: 1px 5px;
  align-self: flex-start;
  white-space: pre;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}

.psm__help-empty {
  font-size: 0.8rem;
  color: var(--color-text-muted);
  font-style: italic;
  padding: 12px 10px;
  margin: 0;
  text-align: center;
}

/* Wrapping toggle group (rollup functions) */
.psm__toggle-group--wrap {
  flex-wrap: wrap;
}

.psm__toggle-group--wrap .psm__toggle-btn {
  flex: unset;
  min-width: 80px;
}

/* ── Footer ──────────────────────────────────────────────────────────────── */
.psm__footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 12px 16px;
  border-top: 1px solid var(--color-border);
  flex-shrink: 0;
}

.psm__btn {
  padding: 7px 14px;
  border-radius: 5px;
  border: none;
  cursor: pointer;
  font-size: 0.85rem;
  font-weight: 500;
  transition: background 0.15s, color 0.15s;
}

.psm__btn--ghost {
  background: transparent;
  color: var(--color-text-muted);
  border: 1px solid var(--color-border);
}

.psm__btn--ghost:hover {
  background: var(--color-hover);
  color: var(--color-text);
}

.psm__btn--primary {
  background: var(--color-accent);
  color: #fff;
}

.psm__btn--primary:hover:not(:disabled) {
  filter: brightness(1.1);
}

.psm__btn--primary:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

/* ── Read-only relation info display ─────────────────────────────────────── */
.psm__readonly-value {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 7px 10px;
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 4px;
  font-size: 0.875rem;
  color: var(--color-text-muted);
  cursor: default;
  user-select: none;
}

.psm__readonly-icon {
  flex-shrink: 0;
  opacity: 0.6;
}

/* ── Relation nuance ───────────────────────────────────────────────────────── */
.psm__nuance-sublabel {
  margin-top: 10px;
}

.psm__nuance-orient {
  display: inline-flex;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  overflow: hidden;
  margin-bottom: 8px;
}

.psm__nuance-orient-btn {
  padding: 4px 10px;
  font-size: 0.8rem;
  background: transparent;
  color: var(--color-text-muted);
  border: none;
  cursor: pointer;
}

.psm__nuance-orient-btn--active {
  background: var(--color-accent, #4663ac);
  color: #fff;
}

.psm__nuance-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.psm__nuance-affix {
  flex: 1;
  min-width: 0;
}

.psm__nuance-word {
  font-size: 0.8rem;
  font-style: italic;
  color: var(--color-text-muted);
  white-space: nowrap;
}
</style>

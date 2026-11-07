/**
 * propertyTypes
 *
 * Single source of truth for all supported property types: their i18n label
 * key, Iconify icon string, group, and readonly flag. Import from here
 * whenever a component needs to iterate over or look up property type info.
 *
 * Moved from frontend/src/components/editor/blocks/properties/propertyTypes.ts
 * to frontend/src/stores/propertyTypes.ts so that it lives next to the
 * database store that owns the PropertySchema type it describes.
 *
 * Groups
 * ------
 * standard  – general-purpose editable properties
 * upload    – file-upload properties
 * formatted – editable text with a specific format / link behaviour
 * computed  – values derived from other properties (formula, rollup)
 * readonly  – system-managed, set automatically by the backend
 */

export interface PropertyTypeDefinition {
  value: string
  /** i18n key, resolved via useI18n() t() */
  labelKey: string
  icon: string
  /** Visual group for the AddSchemaPanel picker. */
  group: 'standard' | 'upload' | 'formatted' | 'computed' | 'readonly'
  /**
   * True for types whose values are written by the backend and must not be
   * changed via the regular upsert endpoint.
   */
  readonly?: boolean
  /**
   * True for types that are UI-only conveniences and map to a different
   * backend type at creation time. AddSchemaPanel handles the mapping.
   * These entries must not appear in getPropertyTypeIcon lookups by stored type.
   */
  virtual?: boolean
  /**
   * True for types whose config is locked after creation — only the name
   * may be changed by the user. Applies to parent_item and sub_item.
   */
  configLocked?: boolean
}

export const PROPERTY_TYPES: readonly PropertyTypeDefinition[] = [
  // ── Standard ──────────────────────────────────────────────────────────────
  { value: 'text',             labelKey: 'db.propType.text',             icon: 'mdi:text',                          group: 'standard' },
  { value: 'number',           labelKey: 'db.propType.number',           icon: 'mdi:numeric',                       group: 'standard' },
  { value: 'checkbox',         labelKey: 'db.propType.checkbox',         icon: 'mdi:checkbox-marked-outline',       group: 'standard' },
  { value: 'select',          labelKey: 'db.propType.select',          icon: 'mdi:format-list-bulleted',          group: 'standard' },
  // Virtual UI type: maps to type='select', config.mode='multiple' at creation time.
  { value: 'select_multiple', labelKey: 'db.propType.select_multiple',  icon: 'mdi:format-list-checks',            group: 'standard', virtual: true },
  { value: 'date',             labelKey: 'db.propType.date',             icon: 'mdi:calendar-outline',              group: 'standard' },
  { value: 'relation',         labelKey: 'db.propType.relation',         icon: 'mdi:link-variant',                  group: 'standard' },
  // Personal reminders. Values live in their own table and are written through
  // /api/alerts; each user sees only their own. At most one per database.
  { value: 'alerts',           labelKey: 'db.propType.alerts',           icon: 'mdi:bell-outline',                  group: 'standard' },

  // ── Upload media ──────────────────────────────────────────────────────────
  { value: 'file',             labelKey: 'db.propType.file',             icon: 'mdi:paperclip',                     group: 'upload' },

  // ── Basic formatted ───────────────────────────────────────────────────────
  { value: 'email',            labelKey: 'db.propType.email',            icon: 'mdi:email-outline',                 group: 'formatted' },
  { value: 'phone',            labelKey: 'db.propType.phone',            icon: 'mdi:phone-outline',                 group: 'formatted' },
  { value: 'url',              labelKey: 'db.propType.url',              icon: 'mdi:link',                          group: 'formatted' },

  // ── Computed (formula / rollup) ───────────────────────────────────────────
  // These types are scaffolded; their cells (FormulaCell, RollupCell) are
  // read-only from the user's perspective – values are produced by the
  // backend. The readonly flag is intentionally NOT set here because the
  // schema itself (expression / aggregation config) is user-editable.
  { value: 'formula',          labelKey: 'db.propType.formula',          icon: 'mdi:function-variant',              group: 'computed' },
  { value: 'rollup',           labelKey: 'db.propType.rollup',           icon: 'mdi:sigma',                         group: 'computed' },

  // ── System readonly ───────────────────────────────────────────────────────
  { value: 'id',               labelKey: 'db.propType.id',               icon: 'mdi:identifier',                    group: 'readonly', readonly: true },
  { value: 'created_by',       labelKey: 'db.propType.created_by',       icon: 'mdi:account-outline',               group: 'readonly', readonly: true },
  { value: 'created_time',     labelKey: 'db.propType.created_time',     icon: 'mdi:clock-plus-outline',            group: 'readonly', readonly: true },
  { value: 'last_edited_by',   labelKey: 'db.propType.last_edited_by',   icon: 'mdi:account-edit-outline',          group: 'readonly', readonly: true },
  { value: 'last_edited_time', labelKey: 'db.propType.last_edited_time', icon: 'mdi:clock-edit-outline',            group: 'readonly', readonly: true },

  // ── Sub-item hierarchy ────────────────────────────────────────────────────
  // parent_item: user-writable (sets the parent via upsert_value), but its
  //              config (partner_schema_id) is locked after creation.
  // sub_item:    user-writable (sets children directly via upsert_value);
  //              also maintained automatically as a mirror whenever
  //              parent_item is written. Config is locked after creation.
  { value: 'parent_item', labelKey: 'db.propType.parent_item', icon: 'mdi:arrow-up-circle-outline',   group: 'readonly', configLocked: true },
  { value: 'sub_item',    labelKey: 'db.propType.sub_item',    icon: 'mdi:arrow-down-circle-outline', group: 'readonly',               configLocked: true },
] as const

/**
 * Return the Iconify icon string for a given property type value,
 * falling back to the text icon. Skips virtual types.
 */
export function getPropertyTypeIcon(type: string): string {
  return PROPERTY_TYPES.find(pt => pt.value === type && !pt.virtual)?.icon ?? 'mdi:text'
}

/**
 * Return the display icon for a concrete PropertySchema.
 *
 * Priority:
 * 1. User-set custom icon stored in config.icon (any Iconify string).
 * 2. Multi-select mode override: select + config.mode === 'multiple'.
 * 3. Type-based default from PROPERTY_TYPES.
 */
export function getSchemaIcon(schema: { type: string; config?: Record<string, unknown> | null }): string {
  const custom = schema.config?.icon as string | undefined
  if (custom) return custom

  if (
    schema.type === 'select' &&
    (schema.config?.mode as string | undefined) === 'multiple'
  ) {
    return 'mdi:format-list-checks'
  }
  return getPropertyTypeIcon(schema.type)
}

/**
 * True for a date property a calendar view lays its entries out by. The
 * server sets ``config.calendarDate`` on it and refuses to delete it, change
 * its type or give it a timeline while a calendar uses it; its entries get a
 * calendar alert each.
 */
export function isCalendarDateSchema(
  schema: { type: string; config?: Record<string, unknown> | null } | null | undefined,
): boolean {
  return schema?.type === 'date' && schema.config?.calendarDate === true
}

/** Roles of the family set a family tree reads. */
export type KinshipRole =
  | 'parents'
  | 'children'
  | 'partners'
  | 'siblings'
  | 'birth'
  | 'death'
  | 'sex'

const KINSHIP_ROLES: readonly KinshipRole[] = [
  'parents', 'children', 'partners', 'siblings', 'birth', 'death', 'sex',
]

/**
 * Names reserved for the roles, per language. The server compares them
 * ignoring case and surrounding spaces and gives a property with such a name
 * its role when the structure fits (``app.blocks.kinship``).
 */
export const KINSHIP_RESERVED_NAMES: Record<KinshipRole, { de: string; en: string }> = {
  parents: { de: 'Eltern', en: 'Parents' },
  children: { de: 'Kinder', en: 'Children' },
  partners: { de: 'Partner', en: 'Partners' },
  siblings: { de: 'Geschwister', en: 'Siblings' },
  birth: { de: 'Geburtsdatum', en: 'Date of birth' },
  death: { de: 'Todeszeitpunkt', en: 'Time of death' },
  sex: { de: 'Geschlecht (Biologisch)', en: 'Sex (biological)' },
}

/** The role whose reserved name *name* is, and in which language. */
export function kinshipRoleForName(
  name: string,
): { role: KinshipRole; language: 'de' | 'en' } | null {
  const folded = name.trim().toLocaleLowerCase()
  if (!folded) return null
  for (const role of KINSHIP_ROLES) {
    for (const language of ['de', 'en'] as const) {
      if (KINSHIP_RESERVED_NAMES[role][language].toLocaleLowerCase() === folded) {
        return { role, language }
      }
    }
  }
  return null
}

/** The structure a new property with a reserved name must have. */
export interface KinshipPreset {
  /** Types the role accepts; the first is preselected. */
  types: string[]
  /** Fixed relation settings, for the relation roles. */
  relation?: {
    direction: 'bilateral' | 'bilateral_self'
    mirrorName: string | null
  }
}

export function kinshipPreset(role: KinshipRole, language: 'de' | 'en'): KinshipPreset {
  switch (role) {
    case 'parents':
      return { types: ['relation'], relation: { direction: 'bilateral', mirrorName: KINSHIP_RESERVED_NAMES.children[language] } }
    case 'children':
      return { types: ['relation'], relation: { direction: 'bilateral', mirrorName: KINSHIP_RESERVED_NAMES.parents[language] } }
    case 'partners':
    case 'siblings':
      return { types: ['relation'], relation: { direction: 'bilateral_self', mirrorName: null } }
    case 'birth':
    case 'death':
      return { types: ['date', 'rollup', 'formula'] }
    case 'sex':
      return { types: ['select'] }
  }
}

type SchemaLike = { type: string; config?: Record<string, unknown> | null } | null | undefined

/**
 * The family set role of a property, or null. The server sets
 * ``config.kinRole`` on a property whose name is reserved for a role and
 * whose structure fits it; such a property keeps its name, type and
 * structure, and cannot be keyed.
 */
export function kinshipRole(schema: SchemaLike): KinshipRole | null {
  const role = schema?.config?.kinRole
  return KINSHIP_ROLES.includes(role as KinshipRole) ? (role as KinshipRole) : null
}

/**
 * True for a relation of the family set. Its nuances are the system nuances
 * the server sends as ``config.nuance.options``; only administrators extend
 * them, in the settings.
 */
export function hasSystemNuances(schema: SchemaLike): boolean {
  return schema?.type === 'relation' && kinshipRole(schema) !== null
}

/** The option a ``sex`` property of the family set reads as female, or null. */
export function kinshipFemaleOptionId(schema: SchemaLike): string | null {
  const id = schema?.config?.kinFemaleOptionId
  return kinshipRole(schema) === 'sex' && typeof id === 'string' ? id : null
}

/** The option a ``sex`` property of the family set reads as male, or null. */
export function kinshipMaleOptionId(schema: SchemaLike): string | null {
  const id = schema?.config?.kinMaleOptionId
  return kinshipRole(schema) === 'sex' && typeof id === 'string' ? id : null
}

/**
 * True for parents, children and siblings of the family set: switching the
 * timeline on at one of them switches it on at the others too.
 */
export function isKinshipTimelineCoupled(schema: SchemaLike): boolean {
  const role = kinshipRole(schema)
  return role === 'parents' || role === 'children' || role === 'siblings'
}

/**
 * True when a property of the family set cannot be deleted: while its
 * database has a family tree view (*viewTypes* lists the types of its views).
 */
export function isKinshipDeleteLocked(schema: SchemaLike, viewTypes: readonly string[]): boolean {
  return kinshipRole(schema) !== null && viewTypes.includes('family_tree')
}

/** Return true if the type is system-managed and cannot be written via the API. */
export function isReadonlyPropertyType(type: string): boolean {
  return PROPERTY_TYPES.find(pt => pt.value === type)?.readonly === true
}

/**
 * Return true if the type's config is locked after creation.
 * Only the name may be changed by the user via PropertySettingsModal.
 * Applies to: parent_item, sub_item.
 */
export function isConfigLockedPropertyType(type: string): boolean {
  return PROPERTY_TYPES.find(pt => pt.value === type)?.configLocked === true
}

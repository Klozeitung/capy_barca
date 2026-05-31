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

<script setup lang="ts">
/**
 * BlockPropertySection
 *
 * Two-column property panel used in both the SideView overlay and the
 * whole-page entry view.  Displays all property schemas of a database
 * (excluding the __name__ pseudo-column) grouped by ``schema.group``.
 *
 * Layout
 * ------
 * Left column:  property name, cogwheel (→ PropertySettingsModal), delete button.
 * Right column: value cell – reuses the same cell components as DatabaseBlock.
 *
 * Groups
 * ------
 * - Default group ("Standard"): always first, NOT foldable, NOT reorderable as a group.
 * - Custom groups: foldable (persistent fold state via block preferences),
 *   reorderable among each other via drag-and-drop (persistent order via
 *   block preferences), double-click on group name to rename.
 * - Properties within any group are drag-and-drop reorderable (persists via
 *   ``schema.position`` PATCH).
 * - Dragging a property between groups updates ``schema.group``.
 *
 * Persistence
 * -----------
 * - Group order:       blockStore.setPreference(databaseId, 'property_group_order', [...])
 * - Group fold state:  blockStore.setPreference(databaseId, 'property_groups_folded', {...})
 * - Property order:    dbStore.updateSchema(databaseId, schemaId, { position })
 * - Property group:    dbStore.updateSchema(databaseId, schemaId, { group })
 *
 * Adding
 * ------
 * - "+New" button per group → opens AddSchemaPanel with pre-filled group.
 * - "+New group" button at bottom → creates a new empty group.
 */
import { ref, computed, watch, nextTick } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { useBlockStore } from '@/stores/blocks'
import {
  useDatabaseStore,
  type PropertySchema,
  type DatabaseEntry,
  type DatabaseView,
} from '@/stores/database'
import { isReadonlyPropertyType, getSchemaIcon } from '@/stores/propertyTypes'
import {
  hideSchemaInAllViews,
  removeGroupFromFolded,
  removeGroupFromOrder,
  schemaIdsInGroup,
} from './propertySectionHelpers'

import AddSchemaPanel from '@/components/editor/blocks/properties/AddSchemaPanel.vue'
import PropertySettingsModal from '@/components/editor/blocks/properties/PropertySettingsModal.vue'
import PropertyVisibilityModal, { type VisibilityMode } from './PropertyVisibilityModal.vue'
import CheckboxCell from '@/components/editor/blocks/properties/cells/CheckboxCell.vue'
import SelectCell from '@/components/editor/blocks/properties/cells/SelectCell.vue'
import MultiSelectCell from '@/components/editor/blocks/properties/cells/MultiSelectCell.vue'
import DateCell from '@/components/editor/blocks/properties/cells/DateCell.vue'
import LinkCell from '@/components/editor/blocks/properties/cells/LinkCell.vue'
import FileCell from '@/components/editor/blocks/properties/cells/FileCell.vue'
import ReadonlyCell from '@/components/editor/blocks/properties/cells/ReadonlyCell.vue'
import TextCell from '@/components/editor/blocks/properties/cells/TextCell.vue'
import RollupCell from '@/components/editor/blocks/properties/cells/RollupCell.vue'
import FormulaCell from '@/components/editor/blocks/properties/cells/FormulaCell.vue'
import RelationCell from '@/components/editor/blocks/properties/cells/RelationCell.vue'

// ── Constants ─────────────────────────────────────────────────────────────────

const DEFAULT_GROUP = 'Standard'
const PREF_GROUP_ORDER = 'property_group_order'
const PREF_GROUPS_FOLDED = 'property_groups_folded'
const PREF_VISIBILITY = 'property_sideview_visibility'
// Shared with DatabaseBlock: the persisted list of database views.
const PREF_VIEWS = 'views'
// Window event consumed by a live DatabaseBlock to hide a freshly added
// property in its in-memory views (#25). Mirrors the constant in DatabaseBlock.
const DB_HIDE_SCHEMA_EVENT = 'capybarca:db-hide-schema-in-views'

// ── Props / emits ─────────────────────────────────────────────────────────────

const props = defineProps<{
  databaseId: string
  entry: DatabaseEntry
}>()

const emit = defineEmits<{
  /** Re-fetch entries after a value mutation (e.g. relation bilateral sync). */
  (e: 'refresh'): void
}>()

// ── Dependencies ──────────────────────────────────────────────────────────────

const { t } = useI18n()
const blockStore = useBlockStore()
const dbStore = useDatabaseStore()

// ── Schemas (excluding __name__) ──────────────────────────────────────────────

const schemas = computed<PropertySchema[]>(() =>
  dbStore.getSchemas(props.databaseId).filter((s) => s.name !== '__name__'),
)

// ── Group order (persisted) ───────────────────────────────────────────────────

const groupOrder = ref<string[]>([])

async function loadGroupOrder(): Promise<void> {
  await blockStore.fetchPreferences(props.databaseId)
  groupOrder.value = blockStore.getPreference<string[]>(
    props.databaseId,
    PREF_GROUP_ORDER,
    [],
  )
}

async function saveGroupOrder(order: string[]): Promise<void> {
  groupOrder.value = order
  await blockStore.setPreference(props.databaseId, PREF_GROUP_ORDER, order)
}

// ── Fold state (persisted) ────────────────────────────────────────────────────

const foldedMap = ref<Record<string, boolean>>({})

async function loadFoldState(): Promise<void> {
  foldedMap.value = blockStore.getPreference<Record<string, boolean>>(
    props.databaseId,
    PREF_GROUPS_FOLDED,
    {},
  )
}

async function toggleFold(groupName: string): Promise<void> {
  foldedMap.value[groupName] = !foldedMap.value[groupName]
  await blockStore.setPreference(props.databaseId, PREF_GROUPS_FOLDED, { ...foldedMap.value })
}

// ── Visibility (persisted, 3-state per schema) ───────────────────────────────

const visibilityMap = ref<Record<string, VisibilityMode>>({})

function loadVisibility(): void {
  visibilityMap.value = blockStore.getPreference<Record<string, VisibilityMode>>(
    props.databaseId,
    PREF_VISIBILITY,
    {},
  )
}

function getVisibility(schemaId: string): VisibilityMode {
  return visibilityMap.value[schemaId] ?? 'show'
}

/**
 * Returns true if a schema should be displayed for the current entry,
 * given its visibility mode.
 */
function isSchemaVisible(schema: PropertySchema): boolean {
  const mode = getVisibility(schema.id)
  if (mode === 'hide') return false
  if (mode === 'hide_empty') {
    const val = props.entry.values[schema.id]
    if (val === undefined || val === null) return false
    // Check for "effectively empty" values.
    if (typeof val === 'object') {
      const v = val as Record<string, unknown>
      // Text/number/url/email/phone: { value: '' }
      if ('value' in v && (v.value === '' || v.value === null || v.value === undefined)) return false
      // Relation: { related_ids: [] }
      if ('related_ids' in v && Array.isArray(v.related_ids) && v.related_ids.length === 0) return false
      // File: { files: [] }
      if ('files' in v && Array.isArray(v.files) && v.files.length === 0) return false
      // Checkbox: { checked: false } – debatable, but false = "empty" makes sense
      if ('checked' in v && v.checked === false) return false
      // Select: { selected: '' } or { selected: [] }
      if ('selected' in v) {
        if (v.selected === '' || v.selected === null) return false
        if (Array.isArray(v.selected) && v.selected.length === 0) return false
      }
    }
  }
  return true
}

// ── Visibility modal ──────────────────────────────────────────────────────────

const showVisibilityModal = ref(false)

function onVisibilityUpdate(newMap: Record<string, VisibilityMode>): void {
  visibilityMap.value = newMap
}

// ── Computed grouped schemas ──────────────────────────────────────────────────

interface SchemaGroup {
  name: string
  isDefault: boolean
  schemas: PropertySchema[]
  folded: boolean
}

const groupedSchemas = computed<SchemaGroup[]>(() => {
  // 1. Collect all group names from schemas.
  const groupMap = new Map<string, PropertySchema[]>()
  for (const s of schemas.value) {
    const g = s.group || DEFAULT_GROUP
    if (!groupMap.has(g)) groupMap.set(g, [])
    groupMap.get(g)!.push(s)
  }

  // Also include empty groups from saved order that have no schemas anymore.
  for (const g of groupOrder.value) {
    if (!groupMap.has(g)) groupMap.set(g, [])
  }

  // 2. Sort schemas within each group by position.
  for (const arr of groupMap.values()) {
    arr.sort((a, b) => a.position - b.position)
  }

  // 2b. Apply visibility filtering per schema.
  for (const [g, arr] of groupMap) {
    groupMap.set(g, arr.filter(isSchemaVisible))
  }

  // 3. Build ordered group list: default first, then by persisted order,
  //    then any remaining groups alphabetically.
  const ordered: SchemaGroup[] = []

  // Default group always first.
  if (groupMap.has(DEFAULT_GROUP)) {
    ordered.push({
      name: DEFAULT_GROUP,
      isDefault: true,
      schemas: groupMap.get(DEFAULT_GROUP)!,
      folded: false, // never foldable
    })
    groupMap.delete(DEFAULT_GROUP)
  }

  // Persisted order for custom groups.
  for (const g of groupOrder.value) {
    if (g === DEFAULT_GROUP) continue
    if (groupMap.has(g)) {
      ordered.push({
        name: g,
        isDefault: false,
        schemas: groupMap.get(g)!,
        folded: !!foldedMap.value[g],
      })
      groupMap.delete(g)
    }
  }

  // Remaining groups not yet in order (newly created via schema).
  const remaining = [...groupMap.keys()].sort()
  for (const g of remaining) {
    ordered.push({
      name: g,
      isDefault: false,
      schemas: groupMap.get(g)!,
      folded: !!foldedMap.value[g],
    })
  }

  return ordered
})

// ── Initialise ────────────────────────────────────────────────────────────────

loadGroupOrder().then(() => { loadFoldState(); loadVisibility() })

watch(() => props.databaseId, () => {
  loadGroupOrder().then(() => { loadFoldState(); loadVisibility() })
})

// ── Active cell (for cells that use is-active pattern) ────────────────────────

interface ActiveCell { schemaId: string }
const activeCell = ref<ActiveCell | null>(null)

function isActiveCell(schemaId: string): boolean {
  return activeCell.value?.schemaId === schemaId
}

function setActiveCell(schemaId: string): void {
  activeCell.value = { schemaId }
}

function clearActiveCell(): void {
  activeCell.value = null
}

function onCellClick(schema: PropertySchema): void {
  if (isReadonlyPropertyType(schema.type)) return
  if (schema.type === 'checkbox' || schema.type === 'relation' || schema.type === 'file') return
  if (schema.type === 'parent_item' || schema.type === 'sub_item') return
  if (schema.type === 'select' && (schema.config?.mode ?? 'single') === 'multiple') return
  setActiveCell(schema.id)
}

// ── Relation change handler ───────────────────────────────────────────────────

async function handleRelationChange(
  schema: PropertySchema,
  value: Record<string, unknown> | null,
): Promise<void> {
  await dbStore.upsertValue(props.databaseId, props.entry.id, schema.id, value)
  if (schema.type === 'parent_item') {
    // sub_item mirrors were updated server-side; refresh so the side view
    // shows the updated parent and the table rebuilds its tree.
    emit('refresh')
    return
  }
  if (schema.type === 'relation' && schema.config?.direction === 'bilateral') {
    await dbStore.fetchSchemas(props.databaseId)
    emit('refresh')
  }
}

// ── Property settings modal ───────────────────────────────────────────────────

const settingsSchema = ref<PropertySchema | null>(null)

function openSettings(schema: PropertySchema): void {
  settingsSchema.value = schema
}

function closeSettings(): void {
  settingsSchema.value = null
}

// ── Delete property ───────────────────────────────────────────────────────────

const confirmDeleteId = ref<string | null>(null)
let confirmDeleteTimer: ReturnType<typeof setTimeout> | null = null

function requestDelete(schemaId: string): void {
  if (confirmDeleteId.value === schemaId) {
    // Second click: actually delete.
    if (confirmDeleteTimer) { clearTimeout(confirmDeleteTimer); confirmDeleteTimer = null }
    confirmDeleteId.value = null
    dbStore.deleteSchema(props.databaseId, schemaId)
    return
  }
  confirmDeleteId.value = schemaId
  if (confirmDeleteTimer) clearTimeout(confirmDeleteTimer)
  confirmDeleteTimer = setTimeout(() => { confirmDeleteId.value = null }, 3000)
}

// ── Add property (per group) ──────────────────────────────────────────────────

const addSchemaForGroup = ref<string | null>(null)

function openAddSchema(groupName: string): void {
  addSchemaForGroup.value = groupName
}

async function onAddSchemaPanelClose(newSchemaId?: string): Promise<void> {
  const targetGroup = addSchemaForGroup.value
  addSchemaForGroup.value = null

  if (!newSchemaId) return

  if (targetGroup && targetGroup !== DEFAULT_GROUP) {
    // Patch the newly created schema to belong to the target group.
    await dbStore.updateSchema(props.databaseId, newSchemaId, { group: targetGroup })
  }

  // #25: A property added from the property section is hidden in *all*
  // database views; the table renders it only after the user opts in via the
  // view settings. (When added from a DatabaseBlock view it is hidden in every
  // view except the active one — that path lives in DatabaseBlock.)
  //
  // 1) Sync any live DatabaseBlock (e.g. the table behind this side panel)
  //    immediately so the new column does not flash into its views.
  window.dispatchEvent(new CustomEvent(DB_HIDE_SCHEMA_EVENT, {
    detail: { databaseId: props.databaseId, schemaId: newSchemaId },
  }))
  // 2) Persist so the change survives where no DatabaseBlock is mounted
  //    (e.g. the full-page entry view).
  await hideNewSchemaInAllViews(newSchemaId)
}

/**
 * Add the given schema to the hiddenColumns of every persisted view so a
 * property created from the side panel does not surface in any table view
 * until explicitly enabled. Preferences are re-fetched first so a concurrently
 * open DatabaseBlock's view edits are not clobbered.
 */
async function hideNewSchemaInAllViews(schemaId: string): Promise<void> {
  await blockStore.fetchPreferences(props.databaseId)
  const stored = blockStore.getPreference<DatabaseView[] | null>(
    props.databaseId,
    PREF_VIEWS,
    null,
  )
  if (!stored || !Array.isArray(stored) || stored.length === 0) return

  const { views, changed } = hideSchemaInAllViews(stored, schemaId)
  if (changed) await blockStore.setPreference(props.databaseId, PREF_VIEWS, views)
}

// ── Add group ─────────────────────────────────────────────────────────────────

const showNewGroupInput = ref(false)
const newGroupName = ref('')

async function addGroup(): Promise<void> {
  const trimmed = newGroupName.value.trim()
  if (!trimmed || trimmed === DEFAULT_GROUP) return
  // Check for duplicate.
  if (groupedSchemas.value.some((g) => g.name === trimmed)) return

  const currentOrder = groupOrder.value.filter((g) => g !== DEFAULT_GROUP)
  currentOrder.push(trimmed)
  await saveGroupOrder(currentOrder)
  showNewGroupInput.value = false
  newGroupName.value = ''
}

function cancelAddGroup(): void {
  showNewGroupInput.value = false
  newGroupName.value = ''
}

// ── Rename group ──────────────────────────────────────────────────────────────

const renamingGroup = ref<string | null>(null)
const renameDraft = ref('')

function startRename(groupName: string): void {
  renamingGroup.value = groupName
  renameDraft.value = groupName
  nextTick(() => {
    const el = document.querySelector<HTMLInputElement>('.bps__group-rename-input')
    el?.select()
  })
}

async function finishRename(): Promise<void> {
  const oldName = renamingGroup.value
  const newName = renameDraft.value.trim()
  renamingGroup.value = null

  if (!oldName || !newName || oldName === newName) return
  if (newName === DEFAULT_GROUP) return
  if (groupedSchemas.value.some((g) => g.name === newName)) return

  // Update all schemas in this group.
  const schemasInGroup = schemas.value.filter((s) => (s.group || DEFAULT_GROUP) === oldName)
  for (const s of schemasInGroup) {
    await dbStore.updateSchema(props.databaseId, s.id, { group: newName })
  }

  // Update group order preference.
  const newOrder = groupOrder.value.map((g) => (g === oldName ? newName : g))
  await saveGroupOrder(newOrder)

  // Update fold state preference.
  if (foldedMap.value[oldName] !== undefined) {
    const newFolded = { ...foldedMap.value }
    newFolded[newName] = newFolded[oldName]
    delete newFolded[oldName]
    foldedMap.value = newFolded
    await blockStore.setPreference(props.databaseId, PREF_GROUPS_FOLDED, newFolded)
  }
}

// ── Delete group ──────────────────────────────────────────────────────────────
//
// Custom groups only. The group itself is removed; its member properties are
// never deleted — they are reassigned to the default group. A two-step confirm
// (click-to-arm, click-again-to-delete, auto-reset after 3s) mirrors the
// per-property delete affordance.

const confirmDeleteGroup = ref<string | null>(null)
let confirmDeleteGroupTimer: ReturnType<typeof setTimeout> | null = null

function requestDeleteGroup(groupName: string): void {
  if (groupName === DEFAULT_GROUP) return
  if (confirmDeleteGroup.value === groupName) {
    if (confirmDeleteGroupTimer) { clearTimeout(confirmDeleteGroupTimer); confirmDeleteGroupTimer = null }
    confirmDeleteGroup.value = null
    deleteGroup(groupName)
    return
  }
  confirmDeleteGroup.value = groupName
  if (confirmDeleteGroupTimer) clearTimeout(confirmDeleteGroupTimer)
  confirmDeleteGroupTimer = setTimeout(() => { confirmDeleteGroup.value = null }, 3000)
}

async function deleteGroup(groupName: string): Promise<void> {
  if (groupName === DEFAULT_GROUP) return

  // Reassign member properties back to the default group.
  const memberIds = schemaIdsInGroup(schemas.value, groupName, DEFAULT_GROUP)
  for (const id of memberIds) {
    await dbStore.updateSchema(props.databaseId, id, { group: DEFAULT_GROUP })
  }

  // Drop the group from the persisted order.
  await saveGroupOrder(removeGroupFromOrder(groupOrder.value, groupName))

  // Drop the group from the fold-state preference if present.
  if (foldedMap.value[groupName] !== undefined) {
    const nextFolded = removeGroupFromFolded(foldedMap.value, groupName)
    foldedMap.value = nextFolded
    await blockStore.setPreference(props.databaseId, PREF_GROUPS_FOLDED, nextFolded)
  }
}

// ── Drag and drop: property reorder within / between groups ───────────────────

const dragSchemaId = ref<string | null>(null)
const dragOverSchemaId = ref<string | null>(null)
const dragOverGroupName = ref<string | null>(null)

function onPropertyDragStart(e: DragEvent, schema: PropertySchema): void {
  dragSchemaId.value = schema.id
  e.dataTransfer!.effectAllowed = 'move'
  e.dataTransfer!.setData('text/plain', schema.id)
}

function onPropertyDragOver(e: DragEvent, targetSchemaId: string, groupName: string): void {
  e.preventDefault()
  e.dataTransfer!.dropEffect = 'move'
  dragOverSchemaId.value = targetSchemaId
  dragOverGroupName.value = groupName
}

function onGroupBodyDragOver(e: DragEvent, groupName: string): void {
  e.preventDefault()
  e.dataTransfer!.dropEffect = 'move'
  dragOverGroupName.value = groupName
  dragOverSchemaId.value = null
}

/**
 * Handles dragover on the group header chip when a *property* is being dragged.
 * This fixes the bug where empty or folded groups cannot receive dropped properties.
 */
function onGroupHeaderPropertyDragOver(e: DragEvent, groupName: string): void {
  if (!dragSchemaId.value) return // only for property drags
  e.preventDefault()
  e.dataTransfer!.dropEffect = 'move'
  dragOverGroupName.value = groupName
  dragOverSchemaId.value = null
}

async function onPropertyDrop(e: DragEvent, targetSchemaId: string | null, groupName: string): Promise<void> {
  e.preventDefault()
  const sourceId = dragSchemaId.value
  dragSchemaId.value = null
  dragOverSchemaId.value = null
  dragOverGroupName.value = null

  if (!sourceId || sourceId === targetSchemaId) return

  const sourceSchema = schemas.value.find((s) => s.id === sourceId)
  if (!sourceSchema) return

  const sourceGroup = sourceSchema.group || DEFAULT_GROUP
  const groupSchemas = schemas.value
    .filter((s) => (s.group || DEFAULT_GROUP) === groupName)
    .sort((a, b) => a.position - b.position)

  // Calculate new position.
  let newPosition: number
  if (!targetSchemaId || groupSchemas.length === 0) {
    // Drop into empty group or at end.
    const maxPos = groupSchemas.length > 0
      ? Math.max(...groupSchemas.map((s) => s.position))
      : 0
    newPosition = maxPos + 1
  } else {
    const targetIdx = groupSchemas.findIndex((s) => s.id === targetSchemaId)
    if (targetIdx === 0) {
      newPosition = groupSchemas[0].position / 2
    } else {
      const before = groupSchemas[targetIdx - 1].position
      const at = groupSchemas[targetIdx].position
      newPosition = (before + at) / 2
    }
  }

  const updates: Record<string, unknown> = { position: newPosition }
  if (sourceGroup !== groupName) {
    updates.group = groupName
  }

  await dbStore.updateSchema(props.databaseId, sourceId, updates)
}

function onPropertyDragEnd(): void {
  dragSchemaId.value = null
  dragOverSchemaId.value = null
  dragOverGroupName.value = null
}

// ── Drag and drop: group reorder ──────────────────────────────────────────────

const dragGroupName = ref<string | null>(null)
const dragOverGroupTarget = ref<string | null>(null)

function onGroupDragStart(e: DragEvent, groupName: string): void {
  dragGroupName.value = groupName
  e.dataTransfer!.effectAllowed = 'move'
  e.dataTransfer!.setData('text/plain', groupName)
}

function onGroupDragOver(e: DragEvent, groupName: string): void {
  if (!dragGroupName.value || dragGroupName.value === groupName) return
  if (groupName === DEFAULT_GROUP) return // cannot drop before default
  e.preventDefault()
  e.dataTransfer!.dropEffect = 'move'
  dragOverGroupTarget.value = groupName
}

async function onGroupDrop(e: DragEvent, targetGroupName: string): Promise<void> {
  e.preventDefault()
  const sourceName = dragGroupName.value
  dragGroupName.value = null
  dragOverGroupTarget.value = null

  if (!sourceName || sourceName === targetGroupName) return
  if (sourceName === DEFAULT_GROUP || targetGroupName === DEFAULT_GROUP) return

  // Build the current custom group order.
  const customGroups = groupedSchemas.value
    .filter((g) => !g.isDefault)
    .map((g) => g.name)

  const fromIdx = customGroups.indexOf(sourceName)
  const toIdx = customGroups.indexOf(targetGroupName)
  if (fromIdx === -1 || toIdx === -1) return

  customGroups.splice(fromIdx, 1)
  customGroups.splice(toIdx, 0, sourceName)

  await saveGroupOrder(customGroups)
}

function onGroupDragEnd(): void {
  dragGroupName.value = null
  dragOverGroupTarget.value = null
}
</script>

<template>
  <div class="bps">
    <!-- Visibility button — floats in the top-right corner of the section,
         revealed on hover so it doesn't compete with the toggle row above. -->
    <button
      class="bps__visibility-btn"
      :title="t('propertySection.visibility.title')"
      @click="showVisibilityModal = true"
    >
      <Icon icon="mdi:eye-outline" width="15" height="15" />
    </button>

    <div
      v-for="group in groupedSchemas"
      :key="group.name"
      class="bps__group"
      :class="{
        'bps__group--drag-over': dragOverGroupTarget === group.name,
        'bps__group--default': group.isDefault,
      }"
      :draggable="!group.isDefault"
      @dragstart.stop="!group.isDefault && onGroupDragStart($event, group.name)"
      @dragover.stop="onGroupDragOver($event, group.name)"
      @drop.stop="onGroupDrop($event, group.name)"
      @dragend="onGroupDragEnd"
    >
      <!-- Group header (custom groups only) — also a property drop target -->
      <div
        v-if="!group.isDefault"
        class="bps__group-header"
        :class="{ 'bps__group-header--drop-target': dragSchemaId && dragOverGroupName === group.name && !dragOverSchemaId }"
        @dragover.stop="onGroupHeaderPropertyDragOver($event, group.name)"
        @drop.stop="onPropertyDrop($event, null, group.name)"
      >
        <button
          class="bps__fold-btn"
          @click="toggleFold(group.name)"
        >
          <Icon
            :icon="group.folded ? 'mdi:chevron-right' : 'mdi:chevron-down'"
            width="16"
            height="16"
          />
        </button>

        <!-- Group name: display or rename input -->
        <input
          v-if="renamingGroup === group.name"
          v-model="renameDraft"
          class="bps__group-rename-input"
          @blur="finishRename"
          @keydown.enter.prevent="finishRename"
          @keydown.escape.prevent="renamingGroup = null"
        />
        <span
          v-else
          class="bps__group-name"
          @dblclick="startRename(group.name)"
        >
          {{ group.name }}
        </span>

        <!-- Delete group (custom groups only) — properties move to default -->
        <button
          class="bps__group-delete-btn"
          :class="{ 'bps__group-delete-btn--confirm': confirmDeleteGroup === group.name }"
          :title="confirmDeleteGroup === group.name
            ? t('propertySection.deleteGroupConfirm')
            : t('propertySection.deleteGroup')"
          @click.stop="requestDeleteGroup(group.name)"
        >
          <Icon icon="mdi:trash-can-outline" width="13" height="13" />
        </button>

        <!-- Drag handle for group -->
        <Icon
          class="bps__group-drag-handle"
          icon="mdi:drag-horizontal-variant"
          width="14"
          height="14"
        />
      </div>

      <!-- Property rows -->
      <div
        v-show="!group.folded"
        class="bps__rows"
        @dragover.prevent="onGroupBodyDragOver($event, group.name)"
        @drop.prevent="onPropertyDrop($event, null, group.name)"
      >
        <div
          v-for="schema in group.schemas"
          :key="schema.id"
          class="bps__row"
          :class="{
            'bps__row--drag-over': dragOverSchemaId === schema.id,
            'bps__row--dragging': dragSchemaId === schema.id,
          }"
          draggable="true"
          @dragstart.stop="onPropertyDragStart($event, schema)"
          @dragover.stop="onPropertyDragOver($event, schema.id, group.name)"
          @drop.stop="onPropertyDrop($event, schema.id, group.name)"
          @dragend="onPropertyDragEnd"
        >
          <!-- Left: property label + actions -->
          <div class="bps__label">
            <Icon
              class="bps__drag-handle"
              icon="mdi:drag-vertical-variant"
              width="14"
              height="14"
            />
            <Icon
              :icon="getSchemaIcon(schema)"
              width="14"
              height="14"
              class="bps__type-icon"
            />
            <span class="bps__prop-name">{{ schema.name }}</span>
            <button
              class="bps__action-btn"
              :title="t('db.settings.title')"
              @click.stop="openSettings(schema)"
            >
              <Icon icon="mdi:cog-outline" width="13" height="13" />
            </button>
            <button
              class="bps__action-btn bps__action-btn--danger"
              :class="{ 'bps__action-btn--confirm': confirmDeleteId === schema.id }"
              :title="confirmDeleteId === schema.id ? t('db.deleteColumnConfirm') : t('actions.delete')"
              @click.stop="requestDelete(schema.id)"
            >
              <Icon icon="mdi:close" width="13" height="13" />
            </button>
          </div>

          <!-- Right: value cell -->
          <div class="bps__cell" @click="onCellClick(schema)">
            <CheckboxCell
              v-if="schema.type === 'checkbox'"
              :entry="entry"
              :schema="schema"
              :database-id="databaseId"
            />

            <SelectCell
              v-else-if="schema.type === 'select' && (schema.config?.mode ?? 'single') === 'single'"
              :entry="entry"
              :schema="schema"
              :database-id="databaseId"
              :is-active="isActiveCell(schema.id)"
              @activate="setActiveCell(schema.id)"
              @deactivate="clearActiveCell"
            />

            <MultiSelectCell
              v-else-if="schema.type === 'select' && schema.config?.mode === 'multiple'"
              :entry="entry"
              :schema="schema"
              :database-id="databaseId"
              :is-active="isActiveCell(schema.id)"
              @activate="setActiveCell(schema.id)"
              @deactivate="clearActiveCell"
            />

            <DateCell
              v-else-if="schema.type === 'date'"
              :entry="entry"
              :schema="schema"
              :database-id="databaseId"
              :is-active="isActiveCell(schema.id)"
              @activate="setActiveCell(schema.id)"
              @deactivate="clearActiveCell"
            />

            <RelationCell
              v-else-if="schema.type === 'relation'"
              :schema="schema"
              :entry="entry"
              :database-id="databaseId"
              @change="handleRelationChange(schema, $event)"
            />

            <RelationCell
              v-else-if="schema.type === 'parent_item'"
              :schema="schema"
              :entry="entry"
              :database-id="databaseId"
              @change="handleRelationChange(schema, $event)"
            />

            <RelationCell
              v-else-if="schema.type === 'sub_item'"
              :schema="schema"
              :entry="entry"
              :database-id="databaseId"
              @change="handleRelationChange(schema, $event)"
            />

            <LinkCell
              v-else-if="schema.type === 'email' || schema.type === 'phone' || schema.type === 'url'"
              :entry="entry"
              :schema="schema"
              :database-id="databaseId"
              :is-active="isActiveCell(schema.id)"
              @activate="setActiveCell(schema.id)"
              @deactivate="clearActiveCell"
            />

            <FileCell
              v-else-if="schema.type === 'file'"
              :entry="entry"
              :schema="schema"
              :database-id="databaseId"
              :is-active="isActiveCell(schema.id)"
              @activate="setActiveCell(schema.id)"
              @deactivate="clearActiveCell"
            />

            <ReadonlyCell
              v-else-if="isReadonlyPropertyType(schema.type)"
              :entry="entry"
              :schema="schema"
            />

            <RollupCell
              v-else-if="schema.type === 'rollup'"
              :entry="entry"
              :schema="schema"
            />

            <FormulaCell
              v-else-if="schema.type === 'formula'"
              :entry="entry"
              :schema="schema"
            />

            <TextCell
              v-else
              :entry="entry"
              :schema="schema"
              :database-id="databaseId"
              :is-active="isActiveCell(schema.id)"
              @activate="setActiveCell(schema.id)"
              @deactivate="clearActiveCell"
            />
          </div>
        </div>

        <!-- Add property button (per group) -->
        <button
          class="bps__add-prop-btn"
          @click="openAddSchema(group.name)"
        >
          <Icon icon="mdi:plus" width="14" height="14" />
          {{ t('propertySection.addProperty') }}
        </button>
      </div>
    </div>

    <!-- Add group button -->
    <div class="bps__add-group">
      <div v-if="showNewGroupInput" class="bps__new-group-row">
        <input
          v-model="newGroupName"
          class="bps__new-group-input"
          :placeholder="t('propertySection.groupNamePlaceholder')"
          autofocus
          @keydown.enter.prevent="addGroup"
          @keydown.escape.prevent="cancelAddGroup"
          @blur="cancelAddGroup"
        />
      </div>
      <button
        v-else
        class="bps__add-group-btn"
        @click="showNewGroupInput = true"
      >
        <Icon icon="mdi:plus" width="14" height="14" />
        {{ t('propertySection.addGroup') }}
      </button>
    </div>

    <!-- Modals -->
    <PropertySettingsModal
      v-if="settingsSchema"
      :schema="settingsSchema"
      :database-id="databaseId"
      @close="closeSettings"
    />

    <AddSchemaPanel
      v-if="addSchemaForGroup !== null"
      :database-id="databaseId"
      @close="onAddSchemaPanelClose"
    />

    <PropertyVisibilityModal
      v-if="showVisibilityModal"
      :database-id="databaseId"
      @close="showVisibilityModal = false"
      @update="onVisibilityUpdate"
    />
  </div>
</template>

<style scoped>
.bps {
  position: relative;
  width: 100%;
  padding: 0 3rem 0.5rem;
}

/* ── Visibility button ──────────────────────────────────────────────────── */

.bps__visibility-btn {
  position: absolute;
  top: 6px;
  right: 3rem;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  border-radius: 5px;
  background: transparent;
  cursor: pointer;
  color: var(--color-text-muted);
  opacity: 0;
  transition: opacity 0.12s, background 0.12s, color 0.12s;
}

.bps:hover .bps__visibility-btn {
  opacity: 1;
}

.bps__visibility-btn:hover {
  background: var(--color-hover);
  color: var(--color-text);
}

/* ── Group ──────────────────────────────────────────────────────────────── */

.bps__group {
  margin-bottom: 2px;
  border-radius: 4px;
  transition: background 0.12s;
}

.bps__group--drag-over {
  background: var(--color-accent-subtle);
}

.bps__group-header {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 0 2px;
  user-select: none;
}

.bps__fold-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  background: none;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  color: var(--color-text-muted);
  transition: background 0.1s;
  flex-shrink: 0;
}

.bps__fold-btn:hover {
  background: var(--color-hover);
}

.bps__group-name {
  font-size: 0.72rem;
  font-weight: 600;
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  cursor: default;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.bps__group-rename-input {
  font-size: 0.72rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--color-text);
  background: var(--color-bg);
  border: 1px solid var(--color-accent);
  border-radius: 3px;
  padding: 1px 4px;
  outline: none;
  flex: 1;
  min-width: 0;
}

.bps__group-drag-handle {
  color: var(--color-text-muted);
  opacity: 0;
  cursor: grab;
  flex-shrink: 0;
  transition: opacity 0.12s;
}

.bps__group-header:hover .bps__group-drag-handle {
  opacity: 0.5;
}

.bps__group-drag-handle:hover {
  opacity: 1 !important;
}

/* ── Delete group button ────────────────────────────────────────────────── */

.bps__group-delete-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  background: none;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  color: var(--color-text-muted);
  opacity: 0;
  flex-shrink: 0;
  transition: opacity 0.12s, background 0.12s, color 0.12s;
}

.bps__group-header:hover .bps__group-delete-btn {
  opacity: 0.6;
}

.bps__group-delete-btn:hover {
  opacity: 1 !important;
  background: var(--color-hover);
  color: #e05555;
}

.bps__group-delete-btn--confirm {
  opacity: 1 !important;
  color: #e05555;
  background: rgba(220, 70, 70, 0.1);
}

.bps__group-header--drop-target {
  background: var(--color-accent-subtle);
  border-radius: 4px;
}

/* ── Property row ───────────────────────────────────────────────────────── */

.bps__rows {
  display: flex;
  flex-direction: column;
}

.bps__row {
  display: flex;
  align-items: stretch;
  min-height: 32px;
  border-radius: 4px;
  transition: background 0.08s;
}

.bps__row:hover {
  background: var(--color-hover);
}

.bps__row--drag-over {
  border-top: 2px solid var(--color-accent);
}

.bps__row--dragging {
  opacity: 0.35;
}

/* ── Left column: label ─────────────────────────────────────────────────── */

.bps__label {
  display: flex;
  align-items: center;
  gap: 4px;
  width: 200px;
  min-width: 140px;
  flex-shrink: 0;
  padding: 4px 6px;
  overflow: hidden;
}

.bps__drag-handle {
  color: var(--color-text-muted);
  opacity: 0;
  cursor: grab;
  flex-shrink: 0;
  transition: opacity 0.12s;
}

.bps__row:hover .bps__drag-handle {
  opacity: 0.5;
}

.bps__type-icon {
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.bps__prop-name {
  font-size: 0.8rem;
  color: var(--color-text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
  min-width: 0;
}

.bps__action-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  background: none;
  border: none;
  border-radius: 3px;
  cursor: pointer;
  color: var(--color-text-muted);
  opacity: 0;
  transition: opacity 0.1s, background 0.1s, color 0.1s;
  flex-shrink: 0;
}

.bps__row:hover .bps__action-btn {
  opacity: 0.6;
}

.bps__action-btn:hover {
  opacity: 1 !important;
  background: var(--color-hover);
}

.bps__action-btn--danger:hover {
  color: #e05555;
}

.bps__action-btn--confirm {
  opacity: 1 !important;
  color: #e05555;
  background: rgba(220, 70, 70, 0.1);
}

/* ── Right column: cell ─────────────────────────────────────────────────── */

.bps__cell {
  flex: 1;
  min-width: 0;
  padding: 4px 6px;
  display: flex;
  align-items: center;
  cursor: pointer;
  font-size: 0.85rem;
}

/*
 * #17: Checkbox cells center themselves for the table layout (.db__checkbox
 * uses margin: 0 auto; the timeline variant centers via flex). In the
 * left-aligned property section they must sit flush left instead of awkwardly
 * in the middle of the value column.
 */
.bps__cell :deep(.db__checkbox) {
  margin-left: 0;
  margin-right: 0;
}

.bps__cell :deep(.db__checkbox-timeline) {
  justify-content: flex-start;
}

/* ── Add property button ────────────────────────────────────────────────── */

.bps__add-prop-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 6px;
  margin-top: 2px;
  background: none;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.8rem;
  color: var(--color-text-muted);
  transition: background 0.1s, color 0.1s;
  width: 100%;
}

.bps__add-prop-btn:hover {
  background: var(--color-hover);
  color: var(--color-text);
}

/* ── Add group ──────────────────────────────────────────────────────────── */

.bps__add-group {
  padding: 4px 0;
}

.bps__add-group-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 6px;
  background: none;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.8rem;
  color: var(--color-text-muted);
  transition: background 0.1s, color 0.1s;
  width: 100%;
}

.bps__add-group-btn:hover {
  background: var(--color-hover);
  color: var(--color-text);
}

.bps__new-group-row {
  padding: 4px 6px;
}

.bps__new-group-input {
  width: 100%;
  font-size: 0.8rem;
  color: var(--color-text);
  background: var(--color-bg);
  border: 1px solid var(--color-accent);
  border-radius: 4px;
  padding: 4px 8px;
  outline: none;
}
</style>

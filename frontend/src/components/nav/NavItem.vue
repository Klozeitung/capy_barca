<script setup lang="ts">
import { computed, onMounted, ref, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { Icon } from '@iconify/vue'
import NavTree from './NavTree.vue'
import IconPicker from '@/components/IconPicker.vue'
import { useBlockStore, type Block } from '@/stores/blocks'
import { useDrag } from '@/composables/useDrag'

const MAX_DEPTH = 8

// Typen, die im NavTree als navigierbare Eltern-Blöcke gelten.
// Muss mit NavTree.NAV_TYPES übereinstimmen.
const NAV_TYPES = new Set(['workspace', 'page', 'database', 'calendar'])

const props = defineProps<{
  block: Block
  depth?: number
}>()

const { t } = useI18n()
const router = useRouter()
const route = useRoute()
const blockStore = useBlockStore()
const drag = useDrag()

const isWorkspace = computed(() => props.block.type === 'workspace')
const isDatabase = computed(() => props.block.type === 'database')
const isCalendar = computed(() => props.block.type === 'calendar')
const isFolded = computed(() => blockStore.getPreference(props.block.id, 'folded', false))
const isActive = computed(
  () => !isWorkspace.value && route.params.blockId === props.block.id,
)

/**
 * Bestimmt, ob der Fold-Button angezeigt werden soll.
 *
 * Für Database-Blöcke gilt: Ihre Kinder (Einträge) werden im NavTree nie
 * angezeigt – der Fold-Button würde also ins Leere gehen und erscheint deshalb
 * nicht. Für alle anderen Typen wird auf die gefilterten NAV_TYPES-Kinder
 * geprüft, damit Einträge in einer Datenbank (page-type) nicht mitzählen.
 */
const hasChildren = computed(() => {
  if (isDatabase.value || isCalendar.value) return false
  if (!blockStore.hasLoadedChildren(props.block.id)) return true
  return blockStore.getChildren(props.block.id).some((b) => NAV_TYPES.has(b.type))
})

const label = computed(() => props.block.content?.title as string | undefined ?? t('nav.untitled'))

const isDragOver = ref(false)
const isDropAbove = ref(false)
const isDropBelow = ref(false)
const showIconPicker = ref(false)
const isCreating = ref(false)
const isRenaming = ref(false)
const renameInput = ref<HTMLInputElement | null>(null)
const renameValue = ref('')

onMounted(async () => {
  await blockStore.fetchPreferences(props.block.id)
})

// ── Fold ──────────────────────────────────────────────────────────────────────

async function toggleFold(e: MouseEvent): Promise<void> {
  e.stopPropagation()
  await blockStore.toggleFolded(props.block.id)
  if (!isFolded.value && !blockStore.hasLoadedChildren(props.block.id)) {
    await blockStore.fetchChildren(props.block.id)
  }
}

// ── Navigation ────────────────────────────────────────────────────────────────

function handleClick(): void {
  if (isWorkspace.value) {
    blockStore.toggleFolded(props.block.id)
    if (!isFolded.value && !blockStore.hasLoadedChildren(props.block.id)) {
      blockStore.fetchChildren(props.block.id)
    }
    return
  }
  router.push(`/blocks/${props.block.id}`)
}

// ── Add child ─────────────────────────────────────────────────────────────────

async function addChild(e: MouseEvent): Promise<void> {
  e.stopPropagation()
  if (isCreating.value) return
  isCreating.value = true
  try {
    const newBlock = await blockStore.createBlock({
      type: 'page',
      parent_id: props.block.id,
      icon: 'mdi:file-document-outline',
      content: { title: t('nav.untitled') },
    })
    if (isFolded.value) {
      await blockStore.setPreference(props.block.id, 'folded', false)
    }
    await blockStore.fetchChildren(props.block.id)
    router.push(`/blocks/${newBlock.id}`)
  } finally {
    isCreating.value = false
  }
}

// ── Rename ────────────────────────────────────────────────────────────────────

async function startRename(e: MouseEvent): Promise<void> {
  e.stopPropagation()
  renameValue.value = props.block.content?.title as string | undefined ?? ''
  isRenaming.value = true
  await nextTick()
  renameInput.value?.select()
}

async function saveRename(): Promise<void> {
  isRenaming.value = false
  const newTitle = renameValue.value.trim()
  if (newTitle === (props.block.content?.title as string | undefined ?? '')) return
  await blockStore.updateBlock(props.block.id, {
    content: { ...props.block.content, title: newTitle },
  })
}

function onRenameKeydown(e: KeyboardEvent): void {
  if (e.key === 'Enter') { e.preventDefault(); saveRename() }
  if (e.key === 'Escape') { isRenaming.value = false }
}

// ── Icon ──────────────────────────────────────────────────────────────────────

function openIconPicker(e: MouseEvent): void {
  e.stopPropagation()
  showIconPicker.value = true
}

async function onIconUpdate(newIcon: string | null): Promise<void> {
  showIconPicker.value = false
  if (!newIcon || newIcon === props.block.icon) return
  await blockStore.updateAppearance(props.block.id, { icon: newIcon })
}

// ── Drag & Drop ───────────────────────────────────────────────────────────────

function onDragStart(e: DragEvent): void {
  e.dataTransfer!.effectAllowed = 'move'
  drag.startDrag(props.block.id, props.block.parent_id, props.block.type)
}

function onDragEnd(): void {
  drag.endDrag()
  isDragOver.value = false
  isDropAbove.value = false
  isDropBelow.value = false
}

function onDragOver(e: DragEvent): void {
  const { blockId, blockType } = drag.getDragging()
  if (!blockId) return

  e.preventDefault()
  e.dataTransfer!.dropEffect = 'move'

  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
  const y = e.clientY - rect.top

  // Workspaces being dragged: only sibling reorder among other workspaces
  if (blockType === 'workspace') {
    if (!isWorkspace.value) {
      onDragLeave()
      return
    }
    if (y < rect.height / 2) {
      isDropAbove.value = true; isDropBelow.value = false; isDragOver.value = false
    } else {
      isDropBelow.value = true; isDropAbove.value = false; isDragOver.value = false
    }
    return
  }

  // Non-page/non-workspace blocks (e.g. database): only drop ON a page target
  if (blockType !== 'page') {
    if (props.block.type !== 'page') {
      onDragLeave()
      return
    }
    isDragOver.value = true; isDropAbove.value = false; isDropBelow.value = false
    return
  }

  // Page blocks: full drop zones (on + above/below)
  if (isWorkspace.value) {
    onDragLeave()
    return
  }
  const zone = rect.height * 0.25
  if (y < zone) {
    isDropAbove.value = true; isDropBelow.value = false; isDragOver.value = false
  } else if (y > rect.height - zone) {
    isDropBelow.value = true; isDropAbove.value = false; isDragOver.value = false
  } else {
    isDragOver.value = true; isDropAbove.value = false; isDropBelow.value = false
  }
}

function onDragLeave(): void {
  isDragOver.value = false
  isDropAbove.value = false
  isDropBelow.value = false
}

async function onDrop(e: DragEvent): Promise<void> {
  e.preventDefault()
  const { blockId, blockType } = drag.getDragging()
  if (!blockId || blockId === props.block.id) { onDragLeave(); return }

  if (blockType === 'workspace') {
    if (isWorkspace.value && (isDropAbove.value || isDropBelow.value)) {
      const siblings = blockStore.getChildren(props.block.parent_id ?? '')
      const idx = siblings.findIndex((s) => s.id === props.block.id)
      if (isDropAbove.value) {
        await drag.dropBetween(
          props.block.parent_id ?? '',
          idx > 0 ? siblings[idx - 1].position : null,
          props.block.position,
        )
      } else {
        await drag.dropBetween(
          props.block.parent_id ?? '',
          props.block.position,
          idx < siblings.length - 1 ? siblings[idx + 1].position : null,
        )
      }
    }
    onDragLeave()
    return
  }

  if (blockType !== 'page') {
    if (isDragOver.value && props.block.type === 'page') {
      await drag.dropOnBlock(props.block.id)
    }
    onDragLeave()
    return
  }

  if (isDragOver.value) {
    await drag.dropOnBlock(props.block.id)
  } else {
    const siblings = blockStore.getChildren(props.block.parent_id ?? '')
    const idx = siblings.findIndex((s) => s.id === props.block.id)
    if (isDropAbove.value) {
      await drag.dropBetween(
        props.block.parent_id ?? '',
        idx > 0 ? siblings[idx - 1].position : null,
        props.block.position,
      )
    } else {
      await drag.dropBetween(
        props.block.parent_id ?? '',
        props.block.position,
        idx < siblings.length - 1 ? siblings[idx + 1].position : null,
      )
    }
  }
  onDragLeave()
}

const defaultIcon = computed(() => {
  if (props.block.type === 'workspace') return 'mdi:home-outline'
  if (props.block.type === 'database') return 'mdi:table-large'
  if (props.block.type === 'calendar') return 'mdi:calendar-outline'
  return 'mdi:file-document-outline'
})

const displayIcon = computed(() => props.block.icon ?? defaultIcon.value)
const indentPx = computed(() => (props.depth ?? 0) * 12 + 8)
</script>

<template>
  <div class="nav-item-wrapper">
    <div v-if="isDropAbove" class="drop-indicator" />

    <div
      class="nav-item"
      :class="{
        'nav-item--active': isActive,
        'nav-item--workspace': isWorkspace,
        'nav-item--drag-over': isDragOver,
      }"
      :style="{ paddingLeft: indentPx + 'px' }"
      draggable="true"
      @click="handleClick"
      @dblclick.stop="startRename"
      @dragstart="onDragStart"
      @dragend="onDragEnd"
      @dragover="onDragOver"
      @dragleave="onDragLeave"
      @drop="onDrop"
    >
      <button
        v-if="hasChildren"
        class="fold-btn"
        :aria-label="isFolded ? 'Aufklappen' : 'Zuklappen'"
        @click.stop="toggleFold"
      >
        <Icon :icon="isFolded ? 'mdi:chevron-right' : 'mdi:chevron-down'" width="14" height="14" />
      </button>
      <span v-else class="fold-btn fold-btn--spacer" aria-hidden="true" />

      <div class="icon-anchor">
        <button class="block-icon-btn" @click.stop="openIconPicker">
          <Icon :icon="displayIcon" width="16" height="16" />
        </button>

        <IconPicker
          v-if="showIconPicker"
          :model-value="block.icon"
          @update:model-value="onIconUpdate"
          @close="showIconPicker = false"
        />
      </div>

      <input
        v-if="isRenaming"
        ref="renameInput"
        v-model="renameValue"
        class="nav-item__rename"
        @blur="saveRename"
        @keydown="onRenameKeydown"
        @click.stop
        @dblclick.stop
      />
      <span
        v-else
        class="nav-item__label"
        :class="{ 'nav-item__label--workspace': isWorkspace }"
      >{{ label }}</span>

      <!-- Add-child-Button nur für Workspace und Page, nicht für Database -->
      <button
        v-if="!isDatabase"
        class="add-child-btn"
        aria-label="Unterseite hinzufügen"
        @click.stop="addChild"
      >
        <Icon icon="mdi:plus" width="14" height="14" />
      </button>
    </div>

    <div v-if="isDropBelow" class="drop-indicator" />

    <NavTree
      v-if="!isFolded && (depth ?? 0) < MAX_DEPTH"
      :parent-id="block.id"
      :depth="(depth ?? 0) + 1"
    />
  </div>
</template>

<style scoped>
.nav-item-wrapper { position: relative; width: 100%; }

.nav-item {
  display: flex;
  align-items: center;
  gap: 4px;
  height: 28px;
  padding-right: 4px;
  border-radius: 4px;
  cursor: pointer;
  user-select: none;
  transition: background 0.1s;
}

.nav-item:hover { background: var(--color-hover); }
.nav-item--active { background: var(--color-active); }
.nav-item--drag-over {
  background: var(--color-accent-subtle);
  outline: 1.5px dashed var(--color-accent);
  outline-offset: -1px;
}

.fold-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  flex-shrink: 0;
  background: none;
  border: none;
  padding: 0;
  border-radius: 3px;
  cursor: pointer;
  color: var(--color-text-muted);
  transition: color 0.1s, background 0.1s;
}

.fold-btn:hover { background: var(--color-border); color: var(--color-text); }
.fold-btn--spacer { pointer-events: none; }

.icon-anchor {
  position: relative;
  flex-shrink: 0;
  display: flex;
  align-items: center;
}

.icon-anchor :deep(.icon-picker) {
  top: calc(100% + 4px);
  left: 0;
}

.block-icon-btn {
  display: flex;
  align-items: center;
  background: none;
  border: none;
  padding: 1px;
  border-radius: 3px;
  cursor: pointer;
  color: var(--color-text-muted);
  transition: color 0.1s, background 0.1s;
  line-height: 0;
}

.block-icon-btn:hover { background: var(--color-border); color: var(--color-text); }

.nav-item__label {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 0.875rem;
  color: var(--color-text);
  min-width: 0;
}

.nav-item__label--workspace { font-weight: 600; }

.nav-item__rename {
  flex: 1;
  min-width: 0;
  background: var(--color-surface);
  border: 1px solid var(--color-accent);
  border-radius: 3px;
  outline: none;
  font-size: 0.875rem;
  font-family: inherit;
  color: var(--color-text);
  padding: 0 4px;
  height: 20px;
}

.add-child-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  flex-shrink: 0;
  background: none;
  border: none;
  padding: 0;
  border-radius: 3px;
  cursor: pointer;
  color: var(--color-text-muted);
  opacity: 0;
  transition: opacity 0.1s, background 0.1s, color 0.1s;
}

.nav-item:hover .add-child-btn { opacity: 1; }
.add-child-btn:hover { background: var(--color-border); color: var(--color-text); }

.drop-indicator {
  height: 2px;
  background: var(--color-accent);
  border-radius: 1px;
  margin: 0 8px;
  pointer-events: none;
}
</style>

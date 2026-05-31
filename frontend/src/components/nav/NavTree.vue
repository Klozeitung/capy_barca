<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import NavItem from './NavItem.vue'
import { useBlockStore } from '@/stores/blocks'

const props = defineProps<{
  parentId: string
  depth?: number
}>()

const blockStore = useBlockStore()

const NAV_TYPES = new Set(['workspace', 'page', 'database', 'calendar'])

const isLoading = computed(() => blockStore.loadingChildren[props.parentId])

// Database-Blöcke zeigen keine Kinder im NavTree – ihre Einträge (page-type
// children) gehören nicht in die Navigation.
const parentIsDatabase = computed(
  () => blockStore.blocks[props.parentId]?.type === 'database',
)

const children = computed(() => {
  if (parentIsDatabase.value) return []
  return blockStore.getChildren(props.parentId).filter((b) => NAV_TYPES.has(b.type))
})

// isFolded wird ausschließlich von NavItem (dem Elternelement) verwaltet.
// NavTree liest den Wert nur reaktiv – es darf ihn nicht selbst per
// fetchPreferences überschreiben, da das den lokalen Toggle-State des
// NavItem rückgängig machen würde (Race Condition → Flicker-Bug).
const isFolded = computed(() => blockStore.getPreference(props.parentId, 'folded', false))

onMounted(async () => {
  // Kinder nur laden, wenn der Parent kein Database-Block ist.
  if (!parentIsDatabase.value && !blockStore.hasLoadedChildren(props.parentId)) {
    await blockStore.fetchChildren(props.parentId)
  }
  // fetchPreferences wird hier bewusst NICHT aufgerufen.
  // NavItem.onMounted ist dafür zuständig und setzt den Wert korrekt,
  // bevor NavTree überhaupt montiert wird.
})

// Re-fetch whenever the cache entry is invalidated (e.g. after moveBlock,
// createBlock, deleteBlock, or an incoming WS event). Without this the nav
// tree goes blank after a drag-and-drop until the user manually folds/unfolds.
watch(
  () => blockStore.childrenMap[props.parentId],
  (val) => {
    if (val === undefined && !parentIsDatabase.value) {
      blockStore.fetchChildren(props.parentId)
    }
  },
)
</script>

<template>
  <div class="nav-tree">
    <template v-if="!isFolded && !parentIsDatabase">
      <template v-if="isLoading">
        <div class="nav-tree__skeleton" :style="{ paddingLeft: (depth ?? 0) * 12 + 8 + 'px' }">
          <span class="skeleton-line" />
        </div>
      </template>
      <template v-else>
        <NavItem
          v-for="block in children"
          :key="block.id"
          :block="block"
          :depth="depth ?? 0"
        />
      </template>
    </template>
  </div>
</template>

<style scoped>
.nav-tree {
  width: 100%;
}

.nav-tree__skeleton {
  display: flex;
  align-items: center;
  height: 28px;
}

.skeleton-line {
  display: block;
  height: 10px;
  width: 60%;
  background: var(--color-border);
  border-radius: 4px;
  opacity: 0.5;
  animation: pulse 1.4s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 0.3; }
  50% { opacity: 0.6; }
}
</style>

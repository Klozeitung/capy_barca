<script setup lang="ts">
/**
 * NavView
 *
 * Renders the sidebar navigation. The workspace block itself is intentionally
 * NOT shown as a nav item – its first-generation children are displayed at
 * depth 0 as if they were top-level roots. This keeps the sidebar clean and
 * avoids a redundant "Workspace" parent row.
 *
 * A "New page" button at the bottom of the scroll area replaces the
 * add-child affordance that previously lived on the workspace NavItem.
 */
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { Icon } from '@iconify/vue'
import NavItem from './NavItem.vue'
import NavAdmin from './NavAdmin.vue'
import TrashModal from './TrashModal.vue'
import { useBlockStore, type Block } from '@/stores/blocks'
import { WORKSPACE_ROOT_ID } from '@/constants'

const { t } = useI18n()
const router = useRouter()
const blockStore = useBlockStore()

// NAV_TYPES must match NavTree / NavItem so the same filter logic is applied.
const NAV_TYPES = new Set(['workspace', 'page', 'database', 'calendar'])

const isCreating = ref(false)
const showTrash = ref(false)

// Reactive list of workspace children that belong in the nav tree.
const rootChildren = computed<Block[]>(() =>
  blockStore
    .getChildren(WORKSPACE_ROOT_ID)
    .filter((b) => b.state === 'active' && NAV_TYPES.has(b.type)),
)

onMounted(async () => {
  await blockStore.fetchChildren(WORKSPACE_ROOT_ID)
  await blockStore.fetchPreferences(WORKSPACE_ROOT_ID)
})

// Re-fetch when the cache is invalidated (WS events, mutations).
watch(
  () => blockStore.childrenMap[WORKSPACE_ROOT_ID],
  (val) => {
    if (val === undefined) {
      blockStore.fetchChildren(WORKSPACE_ROOT_ID)
    }
  },
)

// ── Add top-level page ────────────────────────────────────────────────────────

async function addPage(): Promise<void> {
  if (isCreating.value) return
  isCreating.value = true
  try {
    const newBlock = await blockStore.createBlock({
      type: 'page',
      parent_id: WORKSPACE_ROOT_ID,
      icon: 'mdi:file-document-outline',
      content: { title: t('nav.untitled') },
    })
    await blockStore.fetchChildren(WORKSPACE_ROOT_ID)
    router.push(`/blocks/${newBlock.id}`)
  } finally {
    isCreating.value = false
  }
}
</script>

<template>
  <div class="nav-view">
    <div class="nav-scroll">
      <NavItem
        v-for="block in rootChildren"
        :key="block.id"
        :block="block"
        :depth="0"
      />
    </div>

      <!-- Bottom actions -->
      <div class="nav-footer">
        <button class="nav-footer__add-btn" :disabled="isCreating" @click="addPage">
          <Icon icon="mdi:plus" width="14" height="14" />
          {{ t('nav.addPage') }}
        </button>
        <button class="nav-footer__trash-btn" @click="showTrash = true">
          <Icon icon="mdi:trash-can-outline" width="14" height="14" />
          {{ t('trash.title') }}
        </button>
      </div>

    <NavAdmin />
  </div>

  <TrashModal v-if="showTrash" @close="showTrash = false" />
</template>

<style scoped>
.nav-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.nav-scroll {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 0.5rem 0;
  scrollbar-width: thin;
  scrollbar-color: var(--color-border) transparent;
}

.nav-scroll::-webkit-scrollbar {
  width: 4px;
}

.nav-scroll::-webkit-scrollbar-track {
  background: transparent;
}

.nav-scroll::-webkit-scrollbar-thumb {
  background: var(--color-border);
  border-radius: 2px;
}

/* ── Footer ──────────────────────────────────────────────────────────────── */
.nav-footer {
  padding: 4px 8px 8px;
  border-top: 1px solid var(--color-border);
}

.nav-footer__add-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 8px;
  border: none;
  border-radius: 5px;
  background: none;
  font-size: 0.8125rem;
  color: var(--color-text-muted);
  cursor: pointer;
  transition: background 0.1s, color 0.1s;
  text-align: left;
}

.nav-footer__add-btn:hover:not(:disabled) {
  background: var(--color-hover);
  color: var(--color-text);
}

.nav-footer__add-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.nav-footer__trash-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 8px;
  border: none;
  border-radius: 5px;
  background: none;
  font-size: 0.8125rem;
  color: var(--color-text-muted);
  cursor: pointer;
  transition: background 0.1s, color 0.1s;
  text-align: left;
}

.nav-footer__trash-btn:hover {
  background: var(--color-hover);
  color: var(--color-text);
}
</style>

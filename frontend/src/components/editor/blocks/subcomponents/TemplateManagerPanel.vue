<script setup lang="ts">
/**
 * TemplateManagerPanel
 *
 * Dropdown panel that lists all entry templates for a database and exposes
 * actions to create new ones, open an existing one in the editor, or delete
 * a template via the block store soft-delete.
 *
 * Mounted inside the Templates toolbar button's panel slot in DatabaseBlock.
 */
import { ref, onMounted } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { useDatabaseTemplatesStore, type EntryTemplate } from '@/stores/databaseTemplates'
import { useBlockStore } from '@/stores/blocks'

// ── Props / emits ─────────────────────────────────────────────────────────────

const props = defineProps<{
  databaseId: string
}>()

const emit = defineEmits<{
  (e: 'edit-template', templateId: string): void
  (e: 'close'): void
}>()

// ── Dependencies ──────────────────────────────────────────────────────────────

const { t } = useI18n()
const templateStore = useDatabaseTemplatesStore()
const blockStore = useBlockStore()

// ── Data ──────────────────────────────────────────────────────────────────────

const loading = ref(false)
const creating = ref(false)
/** ID pending two-step delete confirmation. */
const pendingDeleteId = ref<string | null>(null)

async function load(): Promise<void> {
  loading.value = true
  try {
    await templateStore.fetchTemplates(props.databaseId)
  } finally {
    loading.value = false
  }
}

onMounted(() => load())

const templates = () => templateStore.getTemplates(props.databaseId)

// ── Actions ───────────────────────────────────────────────────────────────────

async function createTemplate(): Promise<void> {
  creating.value = true
  try {
    const tmpl = await templateStore.createTemplate(props.databaseId)
    emit('edit-template', tmpl.id)
  } finally {
    creating.value = false
  }
}

function openEditor(template: EntryTemplate): void {
  emit('edit-template', template.id)
}

function templateTitle(template: EntryTemplate): string {
  return (template.content?.title as string | undefined) || t('db.templates.untitled')
}

async function deleteTemplate(template: EntryTemplate): Promise<void> {
  if (pendingDeleteId.value !== template.id) {
    pendingDeleteId.value = template.id
    return
  }
  pendingDeleteId.value = null
  await blockStore.deleteBlock(template.id, props.databaseId)
  await templateStore.fetchTemplates(props.databaseId)
}
</script>

<template>
  <div class="tmp-panel">
    <div class="tmp-panel__header">
      <span class="tmp-panel__title">{{ t('db.templates.title') }}</span>
    </div>

    <div v-if="loading" class="tmp-panel__empty">
      <span class="tmp-panel__spinner" />
    </div>

    <div v-else-if="templates().length === 0" class="tmp-panel__empty">
      {{ t('db.templates.empty') }}
    </div>

    <ul v-else class="tmp-panel__list">
      <li
        v-for="tmpl in templates()"
        :key="tmpl.id"
        class="tmp-panel__item"
      >
        <button class="tmp-panel__item-name" @click="openEditor(tmpl)">
          <Icon
            :icon="tmpl.icon ?? 'mdi:file-document-outline'"
            width="14"
            height="14"
            class="tmp-panel__item-icon"
          />
          <span>{{ templateTitle(tmpl) }}</span>
        </button>
        <button
          class="tmp-panel__delete"
          :class="{ 'tmp-panel__delete--confirm': pendingDeleteId === tmpl.id }"
          :title="pendingDeleteId === tmpl.id ? t('db.templates.deleteConfirm') : t('actions.delete')"
          @click.stop="deleteTemplate(tmpl)"
        >
          <Icon icon="mdi:trash-can-outline" width="13" height="13" />
        </button>
      </li>
    </ul>

    <div class="tmp-panel__footer">
      <button
        class="tmp-panel__create-btn"
        :disabled="creating"
        @click="createTemplate"
      >
        <Icon icon="mdi:plus" width="14" height="14" />
        {{ t('db.templates.new') }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.tmp-panel {
  min-width: 220px;
  max-width: 300px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 7px;
  box-shadow: 0 4px 18px rgba(0, 0, 0, 0.14);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.tmp-panel__header {
  padding: 8px 12px 6px;
  border-bottom: 1px solid var(--color-border);
}

.tmp-panel__title {
  font-size: 0.72rem;
  font-weight: 600;
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.tmp-panel__empty {
  padding: 12px;
  font-size: 0.8125rem;
  color: var(--color-text-muted);
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 40px;
}

.tmp-panel__spinner {
  width: 16px;
  height: 16px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-accent);
  border-radius: 50%;
  animation: tmp-spin 0.7s linear infinite;
}

@keyframes tmp-spin {
  to { transform: rotate(360deg); }
}

.tmp-panel__list {
  list-style: none;
  margin: 0;
  padding: 4px 0;
  max-height: 260px;
  overflow-y: auto;
}

.tmp-panel__item {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 0 6px;
}

.tmp-panel__item-name {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 6px;
  border: none;
  background: none;
  cursor: pointer;
  font-size: 0.8125rem;
  color: var(--color-text);
  text-align: left;
  border-radius: 5px;
  transition: background 0.1s;
  min-width: 0;
}

.tmp-panel__item-name span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tmp-panel__item-name:hover {
  background: var(--color-hover);
}

.tmp-panel__item-icon {
  flex-shrink: 0;
  color: var(--color-text-muted);
}

.tmp-panel__delete {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--color-text-muted);
  cursor: pointer;
  flex-shrink: 0;
  transition: background 0.1s, color 0.1s;
  opacity: 0;
}

.tmp-panel__item:hover .tmp-panel__delete {
  opacity: 1;
}

.tmp-panel__delete:hover {
  background: var(--color-danger-subtle, rgba(211, 47, 47, 0.07));
  color: var(--color-danger, #d32f2f);
}

.tmp-panel__delete--confirm {
  opacity: 1;
  color: var(--color-danger, #d32f2f);
}

.tmp-panel__footer {
  padding: 6px 8px;
  border-top: 1px solid var(--color-border);
}

.tmp-panel__create-btn {
  display: flex;
  align-items: center;
  gap: 5px;
  width: 100%;
  padding: 5px 8px;
  border: none;
  border-radius: 5px;
  background: none;
  cursor: pointer;
  font-size: 0.8125rem;
  color: var(--color-text-muted);
  transition: background 0.1s, color 0.1s;
}

.tmp-panel__create-btn:hover:not(:disabled) {
  background: var(--color-hover);
  color: var(--color-text);
}

.tmp-panel__create-btn:disabled {
  opacity: 0.5;
  cursor: default;
}
</style>

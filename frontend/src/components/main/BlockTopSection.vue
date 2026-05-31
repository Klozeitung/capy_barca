<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Icon } from '@iconify/vue'
import IconPicker from '@/components/IconPicker.vue'
import { useBlockStore, type Block } from '@/stores/blocks'

const props = defineProps<{ block: Block }>()

const { t } = useI18n()
const blockStore = useBlockStore()

const hasCover = computed(() => !!props.block.cover)

const coverStyle = computed(() => {
  if (!props.block.cover) return {}
  if (props.block.cover.startsWith('gradient:')) {
    return { background: props.block.cover.slice('gradient:'.length) }
  }
  return {
    backgroundImage: `url(${props.block.cover})`,
    backgroundSize: 'cover',
    backgroundPosition: 'center',
  }
})

const displayIcon = computed(() => {
  if (props.block.icon) return props.block.icon
  if (props.block.type === 'database') return 'mdi:table-large'
  return 'mdi:file-document-outline'
})

const showIconPicker = ref(false)

async function onIconUpdate(newIcon: string | null): Promise<void> {
  showIconPicker.value = false
  if (!newIcon || newIcon === props.block.icon) return
  await blockStore.updateAppearance(props.block.id, { icon: newIcon })
}

// ── Title ─────────────────────────────────────────────────────────────────────

const editingTitle = ref(false)
const titleInput = ref<string>((props.block.content?.title as string | undefined) ?? '')

watch(
  () => props.block.content?.title,
  (v) => { if (!editingTitle.value) titleInput.value = (v as string | undefined) ?? '' },
)

const title = computed(() => (props.block.content?.title as string | undefined) ?? '')

async function saveTitle(): Promise<void> {
  editingTitle.value = false
  if (titleInput.value === title.value) return
  await blockStore.updateBlock(props.block.id, {
    content: { ...props.block.content, title: titleInput.value },
  })
}

function onTitleKeydown(e: KeyboardEvent): void {
  if (e.key === 'Enter') { e.preventDefault(); saveTitle() }
  if (e.key === 'Escape') { titleInput.value = title.value; editingTitle.value = false }
}
</script>

<template>
  <div class="block-top" :class="{ 'block-top--has-cover': hasCover }">
    <div v-if="hasCover" class="block-cover" :style="coverStyle" />

    <div class="block-meta" :class="{ 'block-meta--with-cover': hasCover }">
      <!-- Icon -->
      <div class="block-icon-wrap">
        <button class="block-icon-btn" @click="showIconPicker = !showIconPicker">
          <Icon :icon="displayIcon" width="40" height="40" />
        </button>

        <IconPicker
          v-if="showIconPicker"
          :model-value="block.icon"
          @update:model-value="onIconUpdate"
          @close="showIconPicker = false"
        />
      </div>

      <!-- Title -->
      <div class="block-title-wrap">
        <textarea
          v-if="editingTitle"
          v-model="titleInput"
          class="block-title-input"
          rows="1"
          autofocus
          @blur="saveTitle"
          @keydown="onTitleKeydown"
        />
        <h1
          v-else
          class="block-title"
          :class="{ 'block-title--empty': !title }"
          tabindex="0"
          @click="editingTitle = true"
          @focus="editingTitle = true"
        >
          {{ title || t('main.untitled') }}
        </h1>
      </div>
    </div>
  </div>
</template>

<style scoped>
.block-top { flex-shrink: 0; width: 100%; }

.block-cover {
  width: 100%;
  height: 250px;
  max-width: 720px;
  margin: 0 auto;
}

.block-meta {
  max-width: 720px;
  margin: 0 auto;
  padding: 2rem 3rem 0.5rem;
}

.block-meta--with-cover { padding-top: 1rem; }

.block-icon-wrap {
  position: relative;
  display: inline-block;
  margin-bottom: 0.5rem;
}

.block-icon-btn {
  background: none;
  border: none;
  padding: 4px;
  border-radius: 6px;
  cursor: pointer;
  color: var(--color-text);
  transition: background 0.1s;
  line-height: 0;
  display: block;
}

.block-icon-btn:hover { background: var(--color-hover); }

.block-icon-wrap :deep(.icon-picker) {
  top: calc(100% + 4px);
  left: 0;
}

.block-title-wrap { width: 100%; }

.block-title {
  font-size: 2.25rem;
  font-weight: 700;
  letter-spacing: -0.02em;
  line-height: 1.2;
  color: var(--color-text);
  margin: 0;
  cursor: text;
  word-break: break-word;
}

.block-title--empty { color: var(--color-text-muted); }

.block-title-input {
  width: 100%;
  font-size: 2.25rem;
  font-weight: 700;
  letter-spacing: -0.02em;
  line-height: 1.2;
  color: var(--color-text);
  background: transparent;
  border: none;
  outline: none;
  resize: none;
  font-family: inherit;
  padding: 0;
  margin: 0;
  overflow: hidden;
  field-sizing: content;
}
</style>

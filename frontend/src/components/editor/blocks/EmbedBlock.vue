<script setup lang="ts">
/**
 * EmbedBlock
 *
 * Renders any embeddable URL inside a sandboxed <iframe>. Two states:
 * URL input (empty) and the live iframe (once a URL is set).
 *
 * Content shape: { url: string }
 *
 * No backend call is needed — the URL is saved directly to block content.
 */
import { computed, ref } from 'vue'
import { Icon } from '@iconify/vue'
import { useBlockStore, type Block } from '@/stores/blocks'

const props = defineProps<{
  block: Block
  parentId: string
}>()

const blockStore = useBlockStore()

const hasUrl = computed(() => Boolean(props.block.content?.url))
const embedUrl = computed(() => props.block.content?.url as string | undefined)

const urlInput = ref('')
const inputError = ref<string | null>(null)

function handleKeydown(e: KeyboardEvent): void {
  if (e.key === 'Enter') {
    e.preventDefault()
    handleConfirm()
  }
}

async function handleConfirm(): Promise<void> {
  const raw = urlInput.value.trim()
  if (!raw) return

  const url = raw.startsWith('http') ? raw : `https://${raw}`
  inputError.value = null

  try {
    new URL(url)
  } catch {
    inputError.value = 'Please enter a valid URL'
    return
  }

  await blockStore.updateBlock(props.block.id, { content: { url } })
  urlInput.value = ''
}

async function handleRemove(): Promise<void> {
  await blockStore.updateBlock(props.block.id, { content: {} })
}
</script>

<template>
  <div class="embed-block">
    <!-- URL input state -->
    <template v-if="!hasUrl">
      <div class="embed-block__input-row">
        <Icon icon="mdi:code-tags" width="16" height="16" class="embed-block__input-icon" />
        <input
          v-model="urlInput"
          type="url"
          class="embed-block__input"
          placeholder="Paste an embed URL and press Enter…"
          @keydown="handleKeydown"
        />
        <button
          class="embed-block__confirm-btn"
          :disabled="!urlInput.trim()"
          @click="handleConfirm"
        >
          Embed
        </button>
      </div>
      <span v-if="inputError" class="embed-block__error">{{ inputError }}</span>
    </template>

    <!-- Iframe state -->
    <template v-else>
      <div class="embed-block__wrapper">
        <div class="embed-block__toolbar">
          <span class="embed-block__url-label">
            <Icon icon="mdi:code-tags" width="13" height="13" />
            {{ embedUrl }}
          </span>
          <a
            :href="embedUrl"
            target="_blank"
            rel="noopener noreferrer"
            class="embed-block__toolbar-btn"
            title="Open in new tab"
          >
            <Icon icon="mdi:open-in-new" width="13" height="13" />
          </a>
          <button class="embed-block__toolbar-btn" title="Remove embed" @click="handleRemove">
            <Icon icon="mdi:close" width="13" height="13" />
          </button>
        </div>
        <iframe
          :src="embedUrl"
          class="embed-block__frame"
          allowfullscreen
          sandbox="allow-scripts allow-same-origin allow-presentation allow-forms"
          loading="lazy"
          title="Embedded content"
        />
      </div>
    </template>
  </div>
</template>

<style scoped>
.embed-block {
  flex: 1;
  min-width: 0;
}

/* ── URL input ───────────────────────────────────────────────────────────── */
.embed-block__input-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: var(--color-surface);
}

.embed-block__input-icon {
  flex-shrink: 0;
  color: var(--color-text-muted);
}

.embed-block__input {
  flex: 1;
  border: none;
  outline: none;
  background: transparent;
  font-size: 0.875rem;
  color: var(--color-text);
  font-family: inherit;
}

.embed-block__input::placeholder {
  color: var(--color-text-muted);
}

.embed-block__confirm-btn {
  flex-shrink: 0;
  padding: 4px 10px;
  border: 1px solid var(--color-border);
  border-radius: 5px;
  background: var(--color-surface);
  color: var(--color-text);
  font-size: 0.75rem;
  cursor: pointer;
  transition: background 0.1s;
}

.embed-block__confirm-btn:hover:not(:disabled) {
  background: var(--color-hover);
}

.embed-block__confirm-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.embed-block__error {
  display: block;
  margin-top: 4px;
  font-size: 0.75rem;
  color: #e05353;
}

/* ── Iframe wrapper ──────────────────────────────────────────────────────── */
.embed-block__wrapper {
  border: 1px solid var(--color-border);
  border-radius: 8px;
  overflow: hidden;
}

.embed-block__toolbar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  background: var(--color-hover);
  border-bottom: 1px solid var(--color-border);
}

.embed-block__url-label {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 0.75rem;
  color: var(--color-text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.embed-block__toolbar-btn {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  padding: 0;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--color-text-muted);
  cursor: pointer;
  text-decoration: none;
  transition: background 0.1s, color 0.1s;
}

.embed-block__toolbar-btn:hover {
  background: var(--color-active);
  color: var(--color-text);
}

.embed-block__frame {
  display: block;
  width: 100%;
  height: 480px;
  border: none;
  background: var(--color-bg);
}
</style>

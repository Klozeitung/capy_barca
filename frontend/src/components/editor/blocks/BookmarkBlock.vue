<script setup lang="ts">
/**
 * BookmarkBlock
 *
 * Two-state component: URL input when empty, rich Open Graph preview card
 * once metadata has been fetched. Calls POST /api/media/bookmark on the
 * backend which scrapes OG tags and returns title, description, image,
 * and favicon.
 *
 * Content shape:
 *   { url, title?, description?, image?, favicon? }
 *
 * Every URL in this content came out of a foreign document or out of whatever
 * was written to the block through the API, and each one turns into something
 * the browser acts on: a link the user clicks, an image it fetches. They are
 * therefore filtered by scheme before they reach the template. The backend
 * applies the same restriction; this is the half that also covers content that
 * never passed through the bookmark endpoint.
 */
import { computed, ref } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { useBlockStore, type Block } from '@/stores/blocks'

const props = defineProps<{
  block: Block
  parentId: string
}>()

const { t } = useI18n()
const blockStore = useBlockStore()

const hasUrl = computed(() => Boolean(props.block.content?.url))
const bUrl = computed(() => props.block.content?.url as string | undefined)
const bTitle = computed(() => props.block.content?.title as string | undefined)
const bDescription = computed(() => props.block.content?.description as string | undefined)
const bImage = computed(() => props.block.content?.image as string | undefined)
const bFavicon = computed(() => props.block.content?.favicon as string | undefined)

// ── URL filtering ─────────────────────────────────────────────────────────────

/** Return *raw* only if it parses and uses one of the permitted schemes. */
function withScheme(raw: string | undefined, allowed: string[]): string | undefined {
  if (!raw) return undefined
  try {
    const parsed = new URL(raw)
    return allowed.includes(parsed.protocol) ? parsed.href : undefined
  } catch {
    return undefined
  }
}

// A link the user clicks: http and https only, so that a stored "javascript:"
// value cannot execute on activation.
const safeHref = computed(() => withScheme(bUrl.value, ['http:', 'https:']))

// Images the browser fetches on its own: https only. That keeps a stored
// "http://192.168.x.x/..." from turning the page into a request into the
// user's network, and drops mixed content the browser would refuse anyway.
const safeImage = computed(() => withScheme(bImage.value, ['https:']))
const safeFavicon = computed(() => withScheme(bFavicon.value, ['https:']))

// ── URL input state ───────────────────────────────────────────────────────────

const urlInput = ref('')
const isFetching = ref(false)
const fetchError = ref<string | null>(null)

async function handleConfirm(): Promise<void> {
  const raw = urlInput.value.trim()
  if (!raw) return

  // Only prepend a scheme when there is none. Matching on "http" alone also
  // accepted values such as "httpfoo:" and passed them through untouched.
  const url = /^https?:\/\//i.test(raw) ? raw : `https://${raw}`
  isFetching.value = true
  fetchError.value = null

  try {
    const res = await fetch('/api/media/bookmark', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    })
    if (res.status === 400) throw new Error(t('block.bookmark.invalidTarget'))
    if (res.status === 429) throw new Error(t('block.bookmark.tooManyRequests'))
    if (!res.ok) throw new Error(t('block.bookmark.fetchFailed'))
    const data = await res.json()
    await blockStore.updateBlock(props.block.id, { content: data })
    urlInput.value = ''
  } catch (e) {
    fetchError.value = e instanceof Error ? e.message : t('block.bookmark.fetchFailed')
  } finally {
    isFetching.value = false
  }
}

function handleKeydown(e: KeyboardEvent): void {
  if (e.key === 'Enter') {
    e.preventDefault()
    handleConfirm()
  }
}

async function handleRemove(): Promise<void> {
  await blockStore.updateBlock(props.block.id, { content: {} })
}

/**
 * Hide a favicon that fails to load.
 *
 * The previous handler read $el, which is not in scope inside a script-setup
 * template, so it threw instead of hiding anything.
 */
function hideBrokenIcon(event: Event): void {
  const img = event.target as HTMLImageElement | null
  if (img) img.style.display = 'none'
}
</script>

<template>
  <div class="bookmark-block">
    <!-- URL input state -->
    <template v-if="!hasUrl">
      <div class="bookmark-block__input-row">
        <Icon icon="mdi:link" width="16" height="16" class="bookmark-block__input-icon" />
        <input
          v-model="urlInput"
          type="url"
          class="bookmark-block__input"
          :placeholder="t('block.bookmark.urlPlaceholder')"
          :disabled="isFetching"
          @keydown="handleKeydown"
        />
        <button
          class="bookmark-block__confirm-btn"
          :disabled="!urlInput.trim() || isFetching"
          @click="handleConfirm"
        >
          <template v-if="isFetching">
            <Icon icon="mdi:loading" width="14" height="14" class="bookmark-block__spinner" />
          </template>
          <template v-else>{{ t('block.bookmark.loadPreview') }}</template>
        </button>
      </div>
      <span v-if="fetchError" class="bookmark-block__error">{{ fetchError }}</span>
    </template>

    <!-- Preview card state -->
    <template v-else>
      <a
        :href="safeHref"
        target="_blank"
        rel="noopener noreferrer"
        class="bookmark-block__card"
      >
        <!-- Text side -->
        <div class="bookmark-block__card-text">
          <span class="bookmark-block__card-title">
            {{ bTitle || bUrl }}
          </span>
          <span v-if="bDescription" class="bookmark-block__card-desc">
            {{ bDescription }}
          </span>
          <span class="bookmark-block__card-meta">
            <img
              v-if="safeFavicon"
              :src="safeFavicon"
              class="bookmark-block__favicon"
              alt=""
              @error="hideBrokenIcon"
            />
            {{ bUrl }}
          </span>
        </div>

        <!-- Thumbnail -->
        <div v-if="safeImage" class="bookmark-block__card-thumb">
          <img :src="safeImage" alt="" class="bookmark-block__thumb-img" />
        </div>
      </a>

      <button
        class="bookmark-block__remove-btn"
        :title="t('block.bookmark.remove')"
        @click="handleRemove"
      >
        <Icon icon="mdi:close" width="14" height="14" />
      </button>
    </template>
  </div>
</template>

<style scoped>
.bookmark-block {
  flex: 1;
  min-width: 0;
  position: relative;
}

/* ── URL input ───────────────────────────────────────────────────────────── */
.bookmark-block__input-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: var(--color-surface);
}

.bookmark-block__input-icon {
  flex-shrink: 0;
  color: var(--color-text-muted);
}

.bookmark-block__input {
  flex: 1;
  border: none;
  outline: none;
  background: transparent;
  font-size: 0.875rem;
  color: var(--color-text);
  font-family: inherit;
}

.bookmark-block__input::placeholder {
  color: var(--color-text-muted);
}

.bookmark-block__input:disabled {
  opacity: 0.5;
}

.bookmark-block__confirm-btn {
  flex-shrink: 0;
  padding: 4px 10px;
  border: 1px solid var(--color-border);
  border-radius: 5px;
  background: var(--color-surface);
  color: var(--color-text);
  font-size: 0.75rem;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 4px;
  transition: background 0.1s;
}

.bookmark-block__confirm-btn:hover:not(:disabled) {
  background: var(--color-hover);
}

.bookmark-block__confirm-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.bookmark-block__error {
  display: block;
  margin-top: 4px;
  font-size: 0.75rem;
  color: #e05353;
}

.bookmark-block__spinner {
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* ── Preview card ────────────────────────────────────────────────────────── */
.bookmark-block__card {
  display: flex;
  align-items: stretch;
  gap: 0;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  overflow: hidden;
  background: var(--color-surface);
  text-decoration: none;
  color: inherit;
  transition: background 0.1s;
}

.bookmark-block__card:hover {
  background: var(--color-hover);
}

.bookmark-block__card-text {
  flex: 1;
  min-width: 0;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.bookmark-block__card-title {
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--color-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.bookmark-block__card-desc {
  font-size: 0.8125rem;
  color: var(--color-text-muted);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.bookmark-block__card-meta {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 0.75rem;
  color: var(--color-text-muted);
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.bookmark-block__favicon {
  width: 14px;
  height: 14px;
  object-fit: contain;
  flex-shrink: 0;
}

.bookmark-block__card-thumb {
  flex-shrink: 0;
  width: 120px;
}

.bookmark-block__thumb-img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

/* ── Remove button ───────────────────────────────────────────────────────── */
.bookmark-block__remove-btn {
  position: absolute;
  top: 6px;
  right: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  padding: 0;
  border: none;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.4);
  color: #fff;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s;
}

.bookmark-block:hover .bookmark-block__remove-btn {
  opacity: 1;
}
</style>

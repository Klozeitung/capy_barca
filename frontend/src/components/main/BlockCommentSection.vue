<script setup lang="ts">
/**
 * BlockCommentSection
 *
 * Displays and manages plain-text comments attached to a block.
 * Rendered beneath BlockPropertySection in both MainView and SideView.
 * The collapsible toggle (with icon and label) lives in the parent
 * (MainView / SideView) — this component renders only the body content.
 *
 * API contract
 * ------------
 * GET    /api/blocks/{blockId}/comments          → CommentResponse[]
 * POST   /api/blocks/{blockId}/comments          → CommentResponse
 * PATCH  /api/blocks/{blockId}/comments/{id}     → CommentResponse
 * DELETE /api/blocks/{blockId}/comments/{id}     → 204
 * GET    /api/users/names                        → { id, username }[]
 *
 * Behaviour
 * ---------
 * - Comments are loaded once on mount and after each mutation.
 * - Author names are resolved from /api/users/names once on mount and
 *   cached for the lifetime of the component instance.
 * - New comment: textarea at the bottom, confirmed with Enter (without
 *   Shift) or the submit button.  Shift+Enter inserts a newline.
 * - Edit in-place: pencil icon switches a comment to a textarea.
 *   Confirmed with the save button or Enter (without Shift); cancelled
 *   with Escape.
 * - Delete: trash icon per comment, with a single confirmation click.
 */
import { ref, onMounted, watch } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'

// ── Props ──────────────────────────────────────────────────────────────────────

const props = defineProps<{
  blockId: string
}>()

// ── i18n ──────────────────────────────────────────────────────────────────────

const { t } = useI18n()

// ── Types ──────────────────────────────────────────────────────────────────────

interface CommentData {
  id: string
  block_id: string
  author_id: string | null
  text: string
  created_at: string
  updated_at: string
}

// ── State ──────────────────────────────────────────────────────────────────────

const comments = ref<CommentData[]>([])
const loading = ref(false)
const error = ref(false)

const newText = ref('')
const submitting = ref(false)

const editingId = ref<string | null>(null)
const editText = ref('')

const pendingDeleteId = ref<string | null>(null)

/** Map of user UUID → username, populated once from /api/users/names. */
const userNames = ref<Map<string, string>>(new Map())

// ── API ────────────────────────────────────────────────────────────────────────

async function loadUserNames(): Promise<void> {
  try {
    const res = await fetch('/api/users/names')
    if (!res.ok) return
    const data: { id: string; username: string }[] = await res.json()
    const map = new Map<string, string>()
    for (const u of data) map.set(u.id, u.username)
    userNames.value = map
  } catch { /* best-effort */ }
}

async function loadComments(): Promise<void> {
  loading.value = true
  error.value = false
  try {
    const res = await fetch(`/api/blocks/${props.blockId}/comments`)
    if (!res.ok) throw new Error(`${res.status}`)
    comments.value = await res.json()
  } catch {
    error.value = true
  } finally {
    loading.value = false
  }
}

async function submitComment(): Promise<void> {
  const text = newText.value.trim()
  if (!text || submitting.value) return
  submitting.value = true
  try {
    const res = await fetch(`/api/blocks/${props.blockId}/comments`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    })
    if (!res.ok) throw new Error(`${res.status}`)
    newText.value = ''
    await loadComments()
  } finally {
    submitting.value = false
  }
}

async function saveEdit(id: string): Promise<void> {
  const text = editText.value.trim()
  if (!text) return
  try {
    const res = await fetch(`/api/blocks/${props.blockId}/comments/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    })
    if (!res.ok) throw new Error(`${res.status}`)
    editingId.value = null
    await loadComments()
  } catch { /* best-effort */ }
}

async function deleteComment(id: string): Promise<void> {
  if (pendingDeleteId.value !== id) {
    pendingDeleteId.value = id
    return
  }
  pendingDeleteId.value = null
  try {
    await fetch(`/api/blocks/${props.blockId}/comments/${id}`, { method: 'DELETE' })
    await loadComments()
  } catch { /* best-effort */ }
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function startEdit(comment: CommentData): void {
  editingId.value = comment.id
  editText.value = comment.text
  pendingDeleteId.value = null
}

function cancelEdit(): void {
  editingId.value = null
  editText.value = ''
}

function onNewKeydown(e: KeyboardEvent): void {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    submitComment()
  }
}

function onEditKeydown(e: KeyboardEvent, id: string): void {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    saveEdit(id)
  }
  if (e.key === 'Escape') cancelEdit()
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function authorName(authorId: string | null): string {
  if (!authorId) return t('commentSection.unknownAuthor')
  return userNames.value.get(authorId) ?? t('commentSection.unknownAuthor')
}

// ── Lifecycle ──────────────────────────────────────────────────────────────────

onMounted(async () => {
  await Promise.all([loadUserNames(), loadComments()])
})
watch(() => props.blockId, () => loadComments())
</script>

<template>
  <div class="bcs">
    <!-- Loading -->
    <div v-if="loading" class="bcs__state">
      <span class="bcs__spinner" />
    </div>

    <!-- Error -->
    <div v-else-if="error" class="bcs__state bcs__state--error">
      {{ t('errors.loadFailed') }}
    </div>

    <template v-else>
      <!-- Comment list -->
      <div v-if="comments.length > 0" class="bcs__list">
        <div
          v-for="comment in comments"
          :key="comment.id"
          class="bcs__item"
          :class="{ 'bcs__item--editing': editingId === comment.id }"
        >
          <div class="bcs__item-meta">
            <span class="bcs__item-author">{{ authorName(comment.author_id) }}</span>
            <span class="bcs__item-date">{{ formatDate(comment.created_at) }}</span>
            <div class="bcs__item-actions">
              <button
                v-if="editingId !== comment.id"
                class="bcs__action-btn"
                :title="t('commentSection.edit')"
                @click="startEdit(comment)"
              >
                <Icon icon="mdi:pencil-outline" width="13" height="13" />
              </button>
              <button
                class="bcs__action-btn"
                :class="{ 'bcs__action-btn--danger': pendingDeleteId === comment.id }"
                :title="pendingDeleteId === comment.id ? t('commentSection.confirmDelete') : t('commentSection.delete')"
                @click="deleteComment(comment.id)"
              >
                <Icon icon="mdi:trash-can-outline" width="13" height="13" />
              </button>
            </div>
          </div>

          <!-- Edit mode -->
          <template v-if="editingId === comment.id">
            <textarea
              v-model="editText"
              class="bcs__textarea"
              rows="3"
              :placeholder="t('commentSection.placeholder')"
              @keydown="onEditKeydown($event, comment.id)"
            />
            <div class="bcs__edit-actions">
              <button class="bcs__btn bcs__btn--primary" @click="saveEdit(comment.id)">
                {{ t('commentSection.save') }}
              </button>
              <button class="bcs__btn" @click="cancelEdit">
                {{ t('actions.cancel') }}
              </button>
            </div>
          </template>

          <!-- Display mode -->
          <p v-else class="bcs__item-text">{{ comment.text }}</p>
        </div>
      </div>

      <p v-else class="bcs__empty">{{ t('commentSection.empty') }}</p>

      <!-- New comment input -->
      <div class="bcs__new">
        <textarea
          v-model="newText"
          class="bcs__textarea"
          rows="2"
          :placeholder="t('commentSection.placeholder')"
          :disabled="submitting"
          @keydown="onNewKeydown"
        />
        <button
          class="bcs__btn bcs__btn--primary"
          :disabled="!newText.trim() || submitting"
          @click="submitComment"
        >
          {{ t('commentSection.submit') }}
        </button>
      </div>
    </template>
  </div>
</template>

<style scoped>
/* ── Container ────────────────────────────────────────────────────────────── */

.bcs {
  padding: 10px 16px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  flex-shrink: 0;
}

/* ── State (loading / error) ────────────────────────────────────────────────── */

.bcs__state {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 12px 0;
  color: var(--color-text-muted);
  font-size: 0.8125rem;
}

.bcs__state--error {
  color: var(--color-danger, #e55);
}

.bcs__spinner {
  display: inline-block;
  width: 16px;
  height: 16px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-accent);
  border-radius: 50%;
  animation: bcs-spin 0.7s linear infinite;
}

@keyframes bcs-spin {
  to { transform: rotate(360deg); }
}

/* ── Comment list ────────────────────────────────────────────────────────────── */

.bcs__list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.bcs__item {
  background: var(--color-hover);
  border-radius: 6px;
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.bcs__item--editing {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
}

.bcs__item-meta {
  display: flex;
  align-items: center;
  gap: 6px;
}

.bcs__item-author {
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--color-text);
  flex-shrink: 0;
}

.bcs__item-date {
  flex: 1;
  font-size: 0.7rem;
  color: var(--color-text-muted);
}

.bcs__item-actions {
  display: flex;
  gap: 2px;
  opacity: 0;
  transition: opacity 0.1s;
}

.bcs__item:hover .bcs__item-actions {
  opacity: 1;
}

.bcs__item-text {
  margin: 0;
  font-size: 0.8125rem;
  color: var(--color-text);
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.5;
}

/* ── Action buttons ──────────────────────────────────────────────────────────── */

.bcs__action-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: none;
  border-radius: 4px;
  background: none;
  cursor: pointer;
  color: var(--color-text-muted);
  transition: background 0.1s, color 0.1s;
  flex-shrink: 0;
}

.bcs__action-btn:hover {
  background: var(--color-border);
  color: var(--color-text);
}

.bcs__action-btn--danger {
  color: var(--color-danger, #e55);
  background: color-mix(in srgb, var(--color-danger, #e55) 12%, transparent);
}

/* ── Empty notice ────────────────────────────────────────────────────────────── */

.bcs__empty {
  margin: 0;
  font-size: 0.8rem;
  color: var(--color-text-muted);
  font-style: italic;
}

/* ── New comment form ────────────────────────────────────────────────────────── */

.bcs__new {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

/* ── Shared textarea ────────────────────────────────────────────────────────── */

.bcs__textarea {
  width: 100%;
  resize: vertical;
  font-size: 0.8125rem;
  font-family: inherit;
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 5px;
  padding: 6px 8px;
  line-height: 1.5;
  box-sizing: border-box;
  transition: border-color 0.12s;
  outline: none;
}

.bcs__textarea:focus {
  border-color: var(--color-accent);
}

.bcs__textarea::placeholder {
  color: var(--color-text-muted);
  opacity: 0.7;
}

.bcs__textarea:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* ── Buttons ─────────────────────────────────────────────────────────────────── */

.bcs__edit-actions {
  display: flex;
  gap: 6px;
}

.bcs__btn {
  padding: 4px 12px;
  border: 1px solid var(--color-border);
  border-radius: 5px;
  background: none;
  cursor: pointer;
  font-size: 0.8rem;
  color: var(--color-text-muted);
  transition: background 0.1s, color 0.1s;
}

.bcs__btn:hover {
  background: var(--color-hover);
  color: var(--color-text);
}

.bcs__btn--primary {
  background: var(--color-accent);
  border-color: var(--color-accent);
  color: #fff;
  align-self: flex-end;
}

.bcs__btn--primary:hover {
  filter: brightness(1.1);
  color: #fff;
}

.bcs__btn--primary:disabled {
  opacity: 0.4;
  cursor: not-allowed;
  filter: none;
}
</style>

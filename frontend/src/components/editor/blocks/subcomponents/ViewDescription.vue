<script setup lang="ts">
/**
 * ViewDescription
 *
 * Free-text description of one database view, rendered between the toolbar
 * and the view content.
 *
 * Interaction mirrors the other inline text fields: a click turns the text
 * into an editable field, and clicking away (blur) saves. Escape discards the
 * edit; Ctrl/Cmd+Enter saves without leaving the keyboard. Enter alone inserts
 * a line break, because a description may span several lines.
 *
 * An empty description shows no placeholder text. It renders as a slim strip
 * that only reveals itself on hover (text cursor and a faint background), so a
 * view without a description looks exactly as before while staying editable.
 *
 * The component only reports the edited text together with the id of its
 * view; normalising and persisting it is the caller's job (see
 * ``useViewManager.saveViewDescription``).
 *
 * With ``readonly`` (a user who may not change the view) the text is shown
 * but does not open for editing, and an empty description renders nothing.
 */
import { ref, computed, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps<{
  /** Id of the view this description belongs to; reported back on save. */
  viewId: string
  /** Stored description of the view; empty when it has none. */
  description: string
  /** Show the description without offering to edit it. */
  readonly?: boolean
}>()

const emit = defineEmits<{
  save: [viewId: string, text: string]
}>()

const { t } = useI18n()

const editing = ref(false)
const draft = ref('')
const textareaEl = ref<HTMLTextAreaElement | null>(null)

/** Set while an Escape is being handled, so the resulting blur does not save. */
let discarding = false

const isEmpty = computed(() => props.description.trim() === '')

/** Grow the field with its content instead of showing a scrollbar. */
function fitHeight(): void {
  const el = textareaEl.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${el.scrollHeight}px`
}

async function startEdit(): Promise<void> {
  if (editing.value || props.readonly) return
  draft.value = props.description
  editing.value = true
  await nextTick()
  const el = textareaEl.value
  if (!el) return
  fitHeight()
  el.focus()
  const end = el.value.length
  el.setSelectionRange(end, end)
}

function commit(): void {
  if (!editing.value) return
  editing.value = false
  if (discarding) {
    discarding = false
    return
  }
  if (draft.value !== props.description) emit('save', props.viewId, draft.value)
}

function discard(): void {
  discarding = true
  textareaEl.value?.blur()
  // The blur handler resets the flag; this covers a field that had already
  // lost focus, where no blur event follows.
  if (editing.value) {
    editing.value = false
    discarding = false
  }
}

function commitFromKeyboard(): void {
  textareaEl.value?.blur()
}
</script>

<template>
  <div v-if="readonly" class="vd">
    <div v-if="!isEmpty" class="vd__display vd__display--static">{{ description }}</div>
  </div>
  <div v-else class="vd" @click.stop>
    <textarea
      v-if="editing"
      ref="textareaEl"
      v-model="draft"
      class="vd__input"
      rows="1"
      :aria-label="t('db.views.description')"
      @input="fitHeight"
      @blur="commit"
      @keydown.escape.prevent="discard"
      @keydown.enter.ctrl.prevent="commitFromKeyboard"
      @keydown.enter.meta.prevent="commitFromKeyboard"
    />
    <div
      v-else
      class="vd__display"
      :class="{ 'vd__display--empty': isEmpty }"
      role="button"
      tabindex="0"
      :aria-label="t('db.views.description')"
      @click="startEdit"
      @keydown.enter.prevent="startEdit"
    >{{ description }}</div>
  </div>
</template>

<style scoped>
.vd {
  margin: -2px 0 6px;
}

.vd__display,
.vd__input {
  display: block;
  width: 100%;
  box-sizing: border-box;
  padding: 3px 6px;
  border-radius: 4px;
  font: inherit;
  font-size: 0.875rem;
  line-height: 1.5;
  color: var(--color-text-muted);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.vd__display {
  cursor: text;
  transition: background 0.12s;
}

.vd__display:hover,
.vd__display:focus-visible {
  background: var(--color-hover);
  outline: none;
}

.vd__display--static,
.vd__display--static:hover {
  cursor: default;
  background: none;
}

/* No placeholder: an empty description is a slim strip, visible on hover only. */
.vd__display--empty {
  min-height: 8px;
  padding-top: 0;
  padding-bottom: 0;
}

.vd__input {
  resize: none;
  overflow: hidden;
  border: none;
  outline: 1px solid var(--color-accent);
  background: transparent;
  color: var(--color-text);
}
</style>

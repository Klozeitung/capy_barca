<script setup lang="ts">
/**
 * RecurrenceActionDialog
 *
 * Compact centered dialog shown when the user edits or deletes an occurrence
 * of a recurring entry.  Lets the user pick the scope:
 *
 *   this      – only the clicked occurrence
 *   following – this and all following occurrences
 *   all       – every occurrence (operates on the master entry)
 *
 * Usage:
 *   <RecurrenceActionDialog
 *     :mode="'edit'"
 *     @this="onThis"
 *     @following="onFollowing"
 *     @all="onAll"
 *     @cancel="onCancel"
 *   />
 */
import { useI18n } from 'vue-i18n'

defineProps<{
  /** 'edit' shows an edit-oriented title; 'delete' shows a deletion title. */
  mode: 'edit' | 'delete'
}>()

const emit = defineEmits<{
  (e: 'this'):      void
  (e: 'following'): void
  (e: 'all'):       void
  (e: 'cancel'):    void
}>()

const { t } = useI18n()
</script>

<template>
  <Teleport to="body">
    <div class="rad-backdrop" @mousedown.self="emit('cancel')">
      <div class="rad" role="dialog" :aria-label="mode === 'edit' ? t('db.calendar.recurEditTitle') : t('db.calendar.recurDeleteTitle')">
        <p class="rad__title">
          {{ mode === 'edit' ? t('db.calendar.recurEditTitle') : t('db.calendar.recurDeleteTitle') }}
        </p>
        <div class="rad__options">
          <button class="rad__opt" @click="emit('this')">
            {{ t('db.calendar.recurThis') }}
          </button>
          <button class="rad__opt" @click="emit('following')">
            {{ t('db.calendar.recurFollowing') }}
          </button>
          <button class="rad__opt" @click="emit('all')">
            {{ t('db.calendar.recurAll') }}
          </button>
        </div>
        <button class="rad__cancel" @click="emit('cancel')">
          {{ t('actions.cancel') }}
        </button>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.rad-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.35);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 410;
}

.rad {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  width: min(320px, 90vw);
  padding: 20px 18px 14px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.25);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.rad__title {
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--color-text);
  margin: 0 0 2px;
}

.rad__options {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.rad__opt {
  text-align: left;
  background: none;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  padding: 9px 13px;
  font-size: 0.82rem;
  color: var(--color-text);
  cursor: pointer;
  transition: background 0.1s, border-color 0.1s;
}

.rad__opt:hover {
  background: var(--color-hover);
  border-color: var(--color-accent);
}

.rad__cancel {
  align-self: flex-end;
  background: none;
  border: none;
  font-size: 0.78rem;
  color: var(--color-text-muted);
  cursor: pointer;
  padding: 4px 6px;
  border-radius: 4px;
  transition: color 0.1s, background 0.1s;
  margin-top: 2px;
}

.rad__cancel:hover {
  color: var(--color-text);
  background: var(--color-hover);
}
</style>

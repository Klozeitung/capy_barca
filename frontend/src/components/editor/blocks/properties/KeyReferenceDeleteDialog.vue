<script setup lang="ts">
/**
 * KeyReferenceDeleteDialog
 *
 * Confirmation shown before deleting a property that other relations are keyed
 * on. Lists each affected relation by name and database, because the point of
 * the pre-flight is to say *which* relations lose their sort order rather than
 * to warn that some do.
 *
 * It appears only when references exist. The ordinary two-step delete
 * affordance stays untouched for the common case, so nothing slows down the
 * path a user takes every day.
 *
 * The dialog does not delete anything itself; it emits ``confirm`` and leaves
 * the call to the host. The backend clears the references regardless of whether
 * this dialog was shown, so a client that skips it cannot leave a dangling
 * pointer behind.
 */
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { useEscapeKey } from '@/composables/useEscapeStack'
import type { KeyReference } from '@/stores/database'

const props = defineProps<{
  references: KeyReference[]
  propertyName: string
}>()

const emit = defineEmits<{
  (e: 'confirm'): void
  (e: 'cancel'): void
}>()

const { t } = useI18n()

useEscapeKey(() => emit('cancel'))

function describe(reference: KeyReference): string {
  return t('db.deleteColumnKeyedItem', {
    property: reference.schema_name,
    database: reference.database_title || t('main.untitled'),
  })
}
</script>

<template>
  <Teleport to="body">
    <div class="krd__backdrop" @click.self="emit('cancel')">
      <div class="krd" role="dialog" aria-modal="true">
        <div class="krd__header">
          <Icon icon="mdi:alert-outline" width="16" height="16" class="krd__icon" />
          <span class="krd__title">{{ t('db.deleteColumnKeyedTitle') }}</span>
        </div>

        <div class="krd__body">
          <p class="krd__text">{{ t('db.deleteColumnKeyedBody') }}</p>
          <ul class="krd__list">
            <li v-for="reference in props.references" :key="reference.schema_id">
              {{ describe(reference) }}
            </li>
          </ul>
        </div>

        <div class="krd__footer">
          <button class="krd__btn krd__btn--ghost" @click="emit('cancel')">
            {{ t('actions.cancel') }}
          </button>
          <button class="krd__btn krd__btn--danger" @click="emit('confirm')">
            {{ t('actions.delete') }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.krd__backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.35);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1200;
}

.krd {
  width: min(420px, calc(100vw - 32px));
  background: var(--color-bg-elevated, var(--color-bg));
  border: 1px solid var(--color-border);
  border-radius: 8px;
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.25);
  display: flex;
  flex-direction: column;
}

.krd__header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--color-border);
}

.krd__icon {
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.krd__title {
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--color-text);
}

.krd__body {
  padding: 12px 14px;
}

.krd__text {
  margin: 0 0 8px;
  font-size: 0.78rem;
  color: var(--color-text);
}

.krd__list {
  margin: 0;
  padding-left: 18px;
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 0.78rem;
  color: var(--color-text-muted);
}

.krd__footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 10px 14px;
  border-top: 1px solid var(--color-border);
}

.krd__btn {
  padding: 5px 12px;
  border-radius: 4px;
  font-size: 0.78rem;
  cursor: pointer;
  border: 1px solid var(--color-border);
  background: transparent;
  color: var(--color-text);
  transition: background 0.1s, border-color 0.1s, color 0.1s;
}

.krd__btn--ghost:hover {
  border-color: var(--color-accent);
  color: var(--color-accent);
}

.krd__btn--danger {
  border-color: #e05555;
  color: #e05555;
}

.krd__btn--danger:hover {
  background: #e05555;
  color: #fff;
}
</style>

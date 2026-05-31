<script setup lang="ts">
/**
 * ReadonlyCell
 *
 * Display-only cell for system-managed property types:
 * id, created_by, created_time, last_edited_by, last_edited_time.
 *
 * These values are written by the backend; the cell has no edit mode.
 * For ``created_by`` and ``last_edited_by``, user UUIDs are resolved to
 * display names via the users store cache.
 */
import { onMounted } from 'vue'
import type { DatabaseEntry, PropertySchema } from '@/stores/database'
import { displayValue } from './cellUtils'
import { useUsersStore } from '@/stores/users'

const props = defineProps<{
  entry: DatabaseEntry
  schema: PropertySchema
}>()

const usersStore = useUsersStore()

// Pre-warm the user cache when a user-attribution cell mounts so the
// display name is available as quickly as possible.
onMounted(() => {
  if (props.schema.type === 'created_by' || props.schema.type === 'last_edited_by') {
    usersStore.loadUsers()
  }
})
</script>

<template>
  <span class="db__cell-value db__cell-value--readonly">
    {{ displayValue(entry, schema, usersStore.resolveUser) }}
  </span>
</template>

<style scoped>
.db__cell-value {
  display: block;
  padding: 7px 12px;
  font-size: 0.875rem;
  min-height: 36px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.db__cell-value--readonly {
  color: var(--color-text-muted);
  font-style: italic;
  cursor: default;
}
</style>

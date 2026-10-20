<script setup lang="ts">
/**
 * DatabaseViewTabs
 *
 * The tab row of a database block: one tab per view, with rename on double
 * click, drag-reorder and the view menu, and the button that adds a view.
 * Presentational: the view state lives in ``useViewManager`` in the parent,
 * which handles every emitted event. Renaming, reordering, the menu and
 * adding need write access (``useEditable``); switching views does not.
 *
 * Styles live in DatabaseBlock.css.
 */
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import type { DatabaseView } from '@/stores/database'
import { useEditable } from '@/composables/useEditable'
import { viewTypeIcon, viewDisplayName } from '@/composables/useViewManager'

defineProps<{
  views: DatabaseView[]
  activeViewId: string
  /** The view being renamed, if any. */
  renamingViewId: string | null
  /** The tab being dragged, and the tab it is over. */
  tabDragId: string | null
  tabDropId: string | null
}>()

/** The name typed while a view is renamed. */
const nameDraft = defineModel<string>('nameDraft', { required: true })

const emit = defineEmits<{
  (e: 'switch', viewId: string): void
  (e: 'start-rename', viewId: string): void
  (e: 'commit-rename', viewId: string): void
  (e: 'cancel-rename'): void
  (e: 'open-menu', viewId: string, event: MouseEvent): void
  (e: 'add'): void
  (e: 'tab-drag-start', event: DragEvent, viewId: string): void
  (e: 'tab-drag-over', event: DragEvent, viewId: string): void
  (e: 'tab-drag-leave', viewId: string): void
  (e: 'tab-drop', event: DragEvent, viewId: string): void
  (e: 'tab-drag-end'): void
}>()

const { t } = useI18n()
const editable = useEditable()
</script>

<template>
  <div class="db__tabs" @click.stop>
    <div
      v-for="view in views"
      :key="view.id"
      class="db__tab"
      :class="{
        'db__tab--active':    view.id === activeViewId,
        'db__tab--dragging':  tabDragId === view.id,
        'db__tab--drag-over': tabDropId === view.id,
      }"
      :draggable="editable"
      @dragstart="emit('tab-drag-start', $event, view.id)"
      @dragover="emit('tab-drag-over', $event, view.id)"
      @dragleave="emit('tab-drag-leave', view.id)"
      @drop="emit('tab-drop', $event, view.id)"
      @dragend="emit('tab-drag-end')"
    >
      <input
        v-if="renamingViewId === view.id"
        v-model="nameDraft"
        class="db__tab-rename-input"
        @blur="emit('commit-rename', view.id)"
        @keydown.enter.prevent="emit('commit-rename', view.id)"
        @keydown.escape.prevent="emit('cancel-rename')"
        @click.stop
      />
      <span
        v-else
        class="db__tab-label"
        @click="emit('switch', view.id)"
        @dblclick.stop="editable && emit('start-rename', view.id)"
      >
        <Icon :icon="viewTypeIcon(view.viewType)" width="12" height="12" class="db__tab-type-icon" />
        {{ viewDisplayName(view, t) }}
      </span>
      <div v-if="editable" class="db__tab-menu-wrap">
        <button
          class="db__tab-menu"
          :title="t('db.views.viewMenu')"
          @click.stop="emit('open-menu', view.id, $event)"
        >
          <Icon icon="mdi:dots-grid" width="12" height="12" />
        </button>
      </div>
    </div>

    <button v-if="editable" class="db__tabs-add" :title="t('db.views.addView')" @click.stop="emit('add')">
      <Icon icon="mdi:plus" width="14" height="14" />
    </button>
  </div>
</template>

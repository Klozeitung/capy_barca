<script setup lang="ts">
/**
 * HelpModal
 *
 * Two-pane help dialog, structurally identical to SettingsModal:
 *   Left  – section navigation
 *   Right – content for the active section
 *
 * Sections
 * --------
 *  blocks    – Block types overview: all available block types grouped by
 *              category, each with its icon, name, and a short description.
 *  shortcuts – Keyboard / mouse shortcut reference for:
 *                • Drag-handle modifier clicks
 *                • Editor keyboard shortcuts
 */
import { ref, computed } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { useHelpModal } from '@/composables/useHelpModal'

const { t } = useI18n()
const { closeHelp } = useHelpModal()

// ── Section navigation ────────────────────────────────────────────────────────

const SECTIONS = [
  { key: 'blocks',      labelKey: 'help.sectionBlocks',      icon: 'mdi:view-grid-outline' },
  { key: 'shortcuts',   labelKey: 'help.sectionShortcuts',   icon: 'mdi:keyboard-outline' },
  { key: 'permissions', labelKey: 'help.sectionPermissions', icon: 'mdi:shield-lock-outline' },
] as const

type SectionKey = typeof SECTIONS[number]['key']

const activeSection = ref<SectionKey>('blocks')

// ── Block type definitions ────────────────────────────────────────────────────

interface BlockEntry {
  icon: string
  nameKey: string
  descKey: string
}

interface BlockGroup {
  labelKey: string
  items: BlockEntry[]
}

const blockGroups = computed<BlockGroup[]>(() => [
  {
    labelKey: 'help.groupText',
    items: [
      { icon: 'mdi:text',                    nameKey: 'block.types.paragraph',         descKey: 'editor.slashMenu.paragraphDesc' },
      { icon: 'mdi:format-header-1',         nameKey: 'block.types.heading_1',         descKey: 'editor.slashMenu.h1Desc' },
      { icon: 'mdi:format-header-2',         nameKey: 'block.types.heading_2',         descKey: 'editor.slashMenu.h2Desc' },
      { icon: 'mdi:format-header-3',         nameKey: 'block.types.heading_3',         descKey: 'editor.slashMenu.h3Desc' },
      { icon: 'mdi:format-header-4',         nameKey: 'block.types.heading_4',         descKey: 'editor.slashMenu.h4Desc' },
      { icon: 'mdi:chevron-right-box-outline', nameKey: 'block.types.heading_1_toggle', descKey: 'editor.slashMenu.h1ToggleDesc' },
      { icon: 'mdi:chevron-right-box-outline', nameKey: 'block.types.heading_2_toggle', descKey: 'editor.slashMenu.h2ToggleDesc' },
      { icon: 'mdi:chevron-right-box-outline', nameKey: 'block.types.heading_3_toggle', descKey: 'editor.slashMenu.h3ToggleDesc' },
      { icon: 'mdi:chevron-right-box-outline', nameKey: 'block.types.heading_4_toggle', descKey: 'editor.slashMenu.h4ToggleDesc' },
      { icon: 'mdi:format-list-bulleted',    nameKey: 'block.types.bulleted_list_item',descKey: 'editor.slashMenu.bulletedListDesc' },
      { icon: 'mdi:format-list-numbered',    nameKey: 'block.types.numbered_list_item',descKey: 'editor.slashMenu.numberedListDesc' },
      { icon: 'mdi:checkbox-marked-outline', nameKey: 'block.types.to_do',             descKey: 'editor.slashMenu.toDoDesc' },
      { icon: 'mdi:chevron-right-box-outline',  nameKey:'block.types.toggle',      descKey: 'editor.slashMenu.toggleDesc' },
      { icon: 'mdi:chevron-right-circle-outline', nameKey: 'block.types.text_toggle', descKey: 'editor.slashMenu.textToggleDesc' },
      { icon: 'mdi:format-quote-close',      nameKey: 'block.types.quote',             descKey: 'editor.slashMenu.quoteDesc' },
      { icon: 'mdi:bell-outline',            nameKey: 'block.types.callout',           descKey: 'editor.slashMenu.calloutDesc' },
      { icon: 'mdi:minus',                   nameKey: 'block.types.divider',           descKey: 'editor.slashMenu.dividerDesc' },
    ],
  },
  {
    labelKey: 'help.groupMedia',
    items: [
      { icon: 'mdi:image-outline',      nameKey: 'block.types.image',    descKey: 'editor.slashMenu.imageDesc' },
      { icon: 'mdi:video-outline',      nameKey: 'block.types.video',    descKey: 'editor.slashMenu.videoDesc' },
      { icon: 'mdi:music-note-outline', nameKey: 'block.types.audio',    descKey: 'editor.slashMenu.audioDesc' },
    ],
  },
  {
    labelKey: 'help.groupFiles',
    items: [
      { icon: 'mdi:file-pdf-box',             nameKey: 'block.types.pdf',      descKey: 'editor.slashMenu.pdfDesc' },
      { icon: 'mdi:paperclip',                nameKey: 'block.types.file',     descKey: 'editor.slashMenu.fileDesc' },
      { icon: 'mdi:folder-multiple-outline',  nameKey: 'block.types.drive',    descKey: 'editor.slashMenu.driveDesc' },
      { icon: 'mdi:bookmark-outline',         nameKey: 'block.types.bookmark', descKey: 'editor.slashMenu.bookmarkDesc' },
      { icon: 'mdi:web',                      nameKey: 'block.types.embed',    descKey: 'editor.slashMenu.embedDesc' },
    ],
  },
  {
    labelKey: 'help.groupStructure',
    items: [
      { icon: 'mdi:view-column-outline',    nameKey: 'block.types.layout',            descKey: 'editor.slashMenu.layoutDesc' },
      { icon: 'mdi:sync',                   nameKey: 'block.types.synched_origin',    descKey: 'editor.slashMenu.synchedOriginDesc' },
      { icon: 'mdi:sync',                   nameKey: 'block.types.synched_mirror',    descKey: 'editor.slashMenu.synchedOriginDesc' },
      { icon: 'mdi:format-list-bulleted',   nameKey: 'block.types.table_of_contents', descKey: 'editor.slashMenu.tableOfContentsDesc' },
      { icon: 'mdi:file-document-outline',  nameKey: 'block.types.page',              descKey: 'editor.slashMenu.pageDesc' },
    ],
  },
  {
    labelKey: 'help.groupDatabase',
    items: [
      { icon: 'mdi:table',                           nameKey: 'block.types.database',        descKey: 'editor.slashMenu.databaseDesc' },
      { icon: 'mdi:table-arrow-right',               nameKey: 'block.types.linked_database', descKey: 'editor.slashMenu.linkedDatabaseDesc' },
      { icon: 'mdi:file-document-multiple-outline',  nameKey: 'db.templates.title',          descKey: 'help.databaseTemplatesDesc' },
    ],
  },
])

// ── Shortcut definitions ──────────────────────────────────────────────────────

interface ShortcutEntry {
  keys: string[]
  descKey: string
}

const handleShortcuts = computed<ShortcutEntry[]>(() => [
  { keys: ['click'],                    descKey: 'help.handleClick' },
  { keys: ['Ctrl', 'click'],            descKey: 'help.handleCtrlClick' },
  { keys: ['Shift', 'click'],           descKey: 'help.handleShiftClick' },
  { keys: ['Ctrl', 'Shift', 'click'],   descKey: 'help.handleCtrlShiftClick' },
  { keys: ['Ctrl', 'Alt', 'click'],     descKey: 'help.handleCtrlAltClick' },
  { keys: ['Ctrl', 'middle click'],     descKey: 'help.handleCtrlMiddleClickMirror' },
])

const editorShortcuts = computed<ShortcutEntry[]>(() => [
  { keys: ['/'],              descKey: 'help.editorSlash' },
  { keys: ['@'],              descKey: 'help.editorAt' },
  { keys: ['Enter'],          descKey: 'help.editorEnter' },
  { keys: ['Shift', 'Enter'], descKey: 'help.editorShiftEnter' },
  { keys: ['Backspace'],      descKey: 'help.editorBackspace' },
  { keys: ['Tab'],            descKey: 'help.editorTab' },
  { keys: ['Shift', 'Tab'],   descKey: 'help.editorShiftTab' },
])

// ── Backdrop ──────────────────────────────────────────────────────────────────

function handleBackdropClick(e: MouseEvent): void {
  if ((e.target as HTMLElement).classList.contains('hm__backdrop')) {
    closeHelp()
  }
}
// ── Permission mode reference data ───────────────────────────────────────────

const permissionModes = [
  { icon: 'mdi:arrow-up-circle-outline', labelKey: 'permissions.modeInherit',    descKey: 'permissions.modeInheritDesc' },
  { icon: 'mdi:earth',                  labelKey: 'permissions.modeEveryone',   descKey: 'permissions.modeEveryoneDesc' },
  { icon: 'mdi:lock-outline',           labelKey: 'permissions.modePrivate',    descKey: 'permissions.modePrivateDesc' },
  { icon: 'mdi:account-multiple-outline', labelKey: 'permissions.modeWhitelist', descKey: 'permissions.modeWhitelistDesc' },
]

</script>

<template>
  <Teleport to="body">
    <div class="hm__backdrop" @click="handleBackdropClick">
      <div class="hm" role="dialog" aria-modal="true" :aria-label="t('help.title')">

        <!-- Header -->
        <div class="hm__header">
          <span class="hm__title">
            <Icon icon="mdi:help-circle-outline" width="16" height="16" />
            {{ t('help.title') }}
          </span>
          <button class="hm__close" :aria-label="t('actions.cancel')" @click="closeHelp">
            <Icon icon="mdi:close" width="16" height="16" />
          </button>
        </div>

        <!-- Body -->
        <div class="hm__body">

          <!-- Left: nav -->
          <nav class="hm__nav">
            <button
              v-for="section in SECTIONS"
              :key="section.key"
              class="hm__nav-item"
              :class="{ 'hm__nav-item--active': activeSection === section.key }"
              @click="activeSection = section.key"
            >
              <Icon :icon="section.icon" width="15" height="15" class="hm__nav-icon" />
              <span>{{ t(section.labelKey) }}</span>
            </button>
          </nav>

          <!-- Right: content -->
          <div class="hm__content">

            <!-- ── Block types ──────────────────────────────────────────── -->
            <template v-if="activeSection === 'blocks'">
              <div
                v-for="group in blockGroups"
                :key="group.labelKey"
                class="hm__group"
              >
                <p class="hm__group-label">{{ t(group.labelKey) }}</p>
                <div class="hm__block-list">
                  <div
                    v-for="item in group.items"
                    :key="item.nameKey"
                    class="hm__block-row"
                  >
                    <span class="hm__block-icon">
                      <Icon :icon="item.icon" width="15" height="15" />
                    </span>
                    <span class="hm__block-name">{{ t(item.nameKey) }}</span>
                    <span class="hm__block-desc">{{ t(item.descKey) }}</span>
                  </div>
                </div>
              </div>
            </template>

            <!-- ── Shortcuts ────────────────────────────────────────────── -->
            <template v-else-if="activeSection === 'shortcuts'">

              <!-- Drag handle -->
              <div class="hm__group">
                <p class="hm__group-label">{{ t('help.shortcutsHandleTitle') }}</p>
                <div class="hm__shortcut-list">
                  <div
                    v-for="entry in handleShortcuts"
                    :key="entry.descKey"
                    class="hm__shortcut-row"
                  >
                    <span class="hm__shortcut-keys">
                      <template v-for="(key, i) in entry.keys" :key="key">
                        <span v-if="i > 0" class="hm__shortcut-plus">+</span>
                        <kbd class="hm__kbd">{{ key }}</kbd>
                      </template>
                    </span>
                    <span class="hm__shortcut-desc">{{ t(entry.descKey) }}</span>
                  </div>
                </div>
              </div>

              <!-- Editor -->
              <div class="hm__group">
                <p class="hm__group-label">{{ t('help.shortcutsEditorTitle') }}</p>
                <div class="hm__shortcut-list">
                  <div
                    v-for="entry in editorShortcuts"
                    :key="entry.descKey"
                    class="hm__shortcut-row"
                  >
                    <span class="hm__shortcut-keys">
                      <template v-for="(key, i) in entry.keys" :key="key">
                        <span v-if="i > 0" class="hm__shortcut-plus">+</span>
                        <kbd class="hm__kbd">{{ key }}</kbd>
                      </template>
                    </span>
                    <span class="hm__shortcut-desc">{{ t(entry.descKey) }}</span>
                  </div>
                </div>
              </div>

            </template>

            <!-- ── Permissions ────────────────────────────────────────── -->
            <template v-else-if="activeSection === 'permissions'">
              <div class="hm__group">
                <p class="hm__group-label">{{ t('help.permissionsTitle') }}</p>
                <p class="hm__permissions-intro">{{ t('help.permissionsIntro') }}</p>
                <div class="hm__perm-modes">
                  <div
                    v-for="item in permissionModes"
                    :key="item.labelKey"
                    class="hm__perm-mode"
                  >
                    <Icon :icon="item.icon" width="14" height="14" class="hm__perm-icon" />
                    <div class="hm__perm-text">
                      <span class="hm__perm-label">{{ t(item.labelKey) }}</span>
                      <span class="hm__perm-desc">{{ t(item.descKey) }}</span>
                    </div>
                  </div>
                </div>
                <p class="hm__permissions-note">{{ t('help.permissionsNote') }}</p>
              </div>
            </template>

          </div>
        </div>

      </div>
    </div>
  </Teleport>
</template>

<style scoped>
/* ── Backdrop ────────────────────────────────────────────────────────────── */
.hm__backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  backdrop-filter: blur(2px);
}

/* ── Dialog ──────────────────────────────────────────────────────────────── */
.hm {
  display: flex;
  flex-direction: column;
  width: min(820px, 92vw);
  height: min(560px, 88vh);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  overflow: hidden;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.35);
}

/* ── Header ──────────────────────────────────────────────────────────────── */
.hm__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 1rem;
  height: 44px;
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.hm__title {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--color-text);
}

.hm__close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 5px;
  background: none;
  cursor: pointer;
  color: var(--color-text-muted);
  transition: background 0.12s, color 0.12s;
}

.hm__close:hover {
  background: var(--color-hover);
  color: var(--color-text);
}

/* ── Body ────────────────────────────────────────────────────────────────── */
.hm__body {
  display: flex;
  flex: 1;
  overflow: hidden;
}

/* ── Nav ─────────────────────────────────────────────────────────────────── */
.hm__nav {
  width: 190px;
  flex-shrink: 0;
  border-right: 1px solid var(--color-border);
  padding: 0.5rem 0.375rem;
  overflow-y: auto;
  background: var(--color-sidebar-bg, var(--color-surface));
}

.hm__nav-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  height: 32px;
  padding: 0 10px;
  border: none;
  border-radius: 5px;
  background: none;
  cursor: pointer;
  font-size: 0.8125rem;
  color: var(--color-text-muted);
  text-align: left;
  transition: background 0.1s, color 0.1s;
}

.hm__nav-item:hover {
  background: var(--color-hover);
  color: var(--color-text);
}

.hm__nav-item--active {
  background: var(--color-active);
  color: var(--color-text);
  font-weight: 500;
}

.hm__nav-icon {
  flex-shrink: 0;
  opacity: 0.75;
}

.hm__nav-item--active .hm__nav-icon {
  opacity: 1;
}

/* ── Content pane ────────────────────────────────────────────────────────── */
.hm__content {
  flex: 1;
  overflow-y: auto;
  padding: 1.25rem 1.5rem;
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

/* ── Group ───────────────────────────────────────────────────────────────── */
.hm__group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.hm__group-label {
  font-size: 0.72rem;
  font-weight: 600;
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin: 0 0 2px;
}

/* ── Block type list ─────────────────────────────────────────────────────── */
.hm__block-list {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.hm__block-row {
  display: grid;
  grid-template-columns: 22px 130px 1fr;
  align-items: center;
  gap: 6px;
  padding: 5px 6px;
  border-radius: 5px;
  font-size: 0.8125rem;
}

.hm__block-row:hover {
  background: var(--color-hover);
}

.hm__block-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.hm__block-name {
  font-weight: 500;
  color: var(--color-text);
  white-space: nowrap;
}

.hm__block-desc {
  color: var(--color-text-muted);
  font-size: 0.775rem;
}

/* ── Shortcut list ───────────────────────────────────────────────────────── */
.hm__shortcut-list {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.hm__shortcut-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 5px 6px;
  border-radius: 5px;
  font-size: 0.8125rem;
}

.hm__shortcut-row:hover {
  background: var(--color-hover);
}

.hm__shortcut-keys {
  display: flex;
  align-items: center;
  gap: 3px;
  flex-shrink: 0;
  min-width: 200px;
}

.hm__shortcut-plus {
  color: var(--color-text-muted);
  font-size: 0.75rem;
  padding: 0 1px;
}

.hm__kbd {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 1px 6px;
  border: 1px solid var(--color-border);
  border-bottom-width: 2px;
  border-radius: 4px;
  background: var(--color-hover);
  color: var(--color-text);
  font-family: inherit;
  font-size: 0.75rem;
  font-weight: 500;
  white-space: nowrap;
}

.hm__shortcut-desc {
  color: var(--color-text-muted);
  font-size: 0.8125rem;
}
/* ── Permissions section ─────────────────────────────────────────────────── */
.hm__permissions-intro {
  font-size: 0.8125rem;
  color: var(--color-text-muted);
  line-height: 1.5;
  margin: 0 0 10px;
}

.hm__perm-modes {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 10px;
}

.hm__perm-mode {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 5px;
  background: var(--color-hover);
}

.hm__perm-icon {
  flex-shrink: 0;
  margin-top: 2px;
  color: var(--color-text-muted);
}

.hm__perm-text {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.hm__perm-label {
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--color-text);
}

.hm__perm-desc {
  font-size: 0.76rem;
  color: var(--color-text-muted);
  line-height: 1.35;
}

.hm__permissions-note {
  font-size: 0.775rem;
  color: var(--color-text-muted);
  margin: 0;
  line-height: 1.45;
}

</style>

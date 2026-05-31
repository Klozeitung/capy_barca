<script setup lang="ts">
/**
 * PageSettingsModal
 *
 * Two-column settings modal for page-level options, mirroring the structure
 * of the global SettingsModal (sidebar nav on the left, content pane on the
 * right).
 *
 * Currently contains one section: "Seite"
 *   - Layout: full-size toggle (expands content to the full available width)
 *   - Cover:  upload / change / remove the page cover image
 *
 * Cover storage convention (server-side):
 *   static/uploads/covers/{pageId}
 * The filename is the page UUID, making it trivial to locate and replace.
 * Uploading a new cover automatically replaces the existing file on disk.
 * Removing the cover calls DELETE /api/blocks/{id}/cover.
 *
 * Full-size state is a client-side preference stored in localStorage under
 * the key `page-fullsize-{pageId}`. It is managed by the parent (MainView)
 * and exposed here via props/emits so the modal stays stateless with respect
 * to that setting.
 */
import { ref } from 'vue'
import { Icon } from '@iconify/vue'
import { useI18n } from 'vue-i18n'
import { useBlockStore, type Block } from '@/stores/blocks'

// ── Props / emits ─────────────────────────────────────────────────────────────

const props = defineProps<{
  block: Block
  /** Current full-size state, controlled by the parent. */
  fullSize: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'update:fullSize', value: boolean): void
}>()

// ── Dependencies ──────────────────────────────────────────────────────────────

const { t } = useI18n()
const blockStore = useBlockStore()

// ── Sidebar sections ──────────────────────────────────────────────────────────

const SECTIONS = [
  { key: 'page',   labelKey: 'pageSettings.sectionPage',   icon: 'mdi:file-document-outline' },
  { key: 'export', labelKey: 'pageSettings.sectionExport', icon: 'mdi:export-variant' },
] as const

type SectionKey = typeof SECTIONS[number]['key']
const activeSection = ref<SectionKey>('page')

// ── Full-size toggle ──────────────────────────────────────────────────────────

function toggleFullSize(): void {
  emit('update:fullSize', !props.fullSize)
}

// ── Cover management ──────────────────────────────────────────────────────────

const coverFileInput = ref<HTMLInputElement | null>(null)
const coverUploading = ref(false)
const coverError = ref<string | null>(null)
const coverRemoving = ref(false)

function openFilePicker(): void {
  coverError.value = null
  coverFileInput.value?.click()
}

async function onCoverFileSelected(e: Event): Promise<void> {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!input) return
  // Reset so the same file can be re-selected after removal.
  input.value = ''
  if (!file) return

  coverError.value = null
  coverUploading.value = true
  try {
    const formData = new FormData()
    formData.append('file', file)

    // POST /api/blocks/{id}/cover — server saves the file as
    // static/uploads/covers/{pageId} (overwriting any previous cover).
    // The response is the updated block.
    const res = await fetch(`/api/blocks/${props.block.id}/cover`, {
      method: 'POST',
      body: formData,
      credentials: 'include',
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    // Re-fetch the block so the store reflects the new cover URL.
    await blockStore.fetchBlock(props.block.id)
  } catch {
    coverError.value = t('pageSettings.coverUploadError')
  } finally {
    coverUploading.value = false
  }
}

async function removeCover(): Promise<void> {
  coverError.value = null
  coverRemoving.value = true
  try {
    // DELETE /api/blocks/{id}/cover — server removes the file from disk.
    const res = await fetch(`/api/blocks/${props.block.id}/cover`, {
      method: 'DELETE',
      credentials: 'include',
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    // Re-fetch the block so the store clears the cover field.
    await blockStore.fetchBlock(props.block.id)
  } catch {
    coverError.value = t('pageSettings.coverRemoveError')
  } finally {
    coverRemoving.value = false
  }
}

// ── Backdrop click ────────────────────────────────────────────────────────────

function onBackdropClick(e: MouseEvent): void {
  if ((e.target as HTMLElement).classList.contains('psm__backdrop')) {
    emit('close')
  }
}

// ── Export ────────────────────────────────────────────────────────────────────

function exportAsPdf(): void {
  emit('close')
  setTimeout(() => {
    // beforeprint fires after print-CSS is applied — scrollHeight
    // reflects the print font size and layout at that point.
    function handleBeforePrint() {
      document.querySelectorAll<HTMLTextAreaElement>('.editor-row__textarea').forEach(ta => {
        ta.style.height = 'auto'
        ta.style.height = ta.scrollHeight + 'px'
      })
    }
    function handleAfterPrint() {
      // Dispatch input so Vue's auto-resize restores screen heights.
      document.querySelectorAll<HTMLTextAreaElement>('.editor-row__textarea').forEach(ta => {
        ta.dispatchEvent(new Event('input'))
      })
      window.removeEventListener('beforeprint', handleBeforePrint)
      window.removeEventListener('afterprint', handleAfterPrint)
    }
    window.addEventListener('beforeprint', handleBeforePrint)
    window.addEventListener('afterprint', handleAfterPrint)
    window.print()
  }, 150)
}
</script>

<template>
  <Teleport to="body">
    <div class="psm__backdrop" @click="onBackdropClick">
      <div class="psm" role="dialog" aria-modal="true" :aria-label="t('pageSettings.title')">

        <!-- Header -->
        <div class="psm__header">
          <span class="psm__header-title">
            <Icon icon="mdi:file-cog-outline" width="15" height="15" />
            {{ t('pageSettings.title') }}
          </span>
          <button class="psm__close" :aria-label="t('actions.cancel')" @click="emit('close')">
            <Icon icon="mdi:close" width="15" height="15" />
          </button>
        </div>

        <!-- Body -->
        <div class="psm__body">

          <!-- Left sidebar -->
          <nav class="psm__sidebar">
            <button
              v-for="section in SECTIONS"
              :key="section.key"
              class="psm__nav-item"
              :class="{ 'psm__nav-item--active': activeSection === section.key }"
              @click="activeSection = section.key"
            >
              <Icon :icon="section.icon" width="14" height="14" class="psm__nav-icon" />
              <span>{{ t(section.labelKey) }}</span>
            </button>
          </nav>

          <!-- Right content pane -->
          <div class="psm__content">

            <!-- ── Seite ──────────────────────────────────────────────── -->
            <template v-if="activeSection === 'page'">

              <!-- Layout section -->
              <div class="psm__section">
                <p class="psm__section-title">{{ t('pageSettings.layoutTitle') }}</p>

                <!-- Full-size toggle -->
                <div class="psm__row">
                  <div class="psm__row-label">
                    <span class="psm__row-name">{{ t('pageSettings.fullSize') }}</span>
                    <span class="psm__row-hint">{{ t('pageSettings.fullSizeHint') }}</span>
                  </div>
                  <button
                    class="psm__toggle"
                    :class="{ 'psm__toggle--on': fullSize }"
                    :aria-pressed="fullSize"
                    @click="toggleFullSize"
                  >
                    <span class="psm__toggle-thumb" />
                  </button>
                </div>
              </div>

              <!-- Cover section -->
              <div class="psm__section">
                <p class="psm__section-title">{{ t('pageSettings.coverTitle') }}</p>

                <!-- Current cover preview -->
                <div v-if="block.cover" class="psm__cover-preview">
                  <div
                    class="psm__cover-image"
                    :style="
                      block.cover.startsWith('gradient:')
                        ? { background: block.cover.slice('gradient:'.length) }
                        : { backgroundImage: `url(${block.cover})` }
                    "
                  />
                </div>

                <!-- Actions -->
                <div class="psm__cover-actions">
                  <button
                    class="psm__btn"
                    :disabled="coverUploading"
                    @click="openFilePicker"
                  >
                    <Icon
                      v-if="coverUploading"
                      icon="mdi:loading"
                      width="14"
                      height="14"
                      class="spin"
                    />
                    <Icon v-else icon="mdi:image-plus-outline" width="14" height="14" />
                    {{ block.cover ? t('pageSettings.coverChange') : t('pageSettings.coverUpload') }}
                  </button>

                  <button
                    v-if="block.cover"
                    class="psm__btn psm__btn--danger"
                    :disabled="coverRemoving"
                    @click="removeCover"
                  >
                    <Icon
                      v-if="coverRemoving"
                      icon="mdi:loading"
                      width="14"
                      height="14"
                      class="spin"
                    />
                    <Icon v-else icon="mdi:image-remove-outline" width="14" height="14" />
                    {{ t('pageSettings.coverRemove') }}
                  </button>
                </div>

                <!-- Error feedback -->
                <p v-if="coverError" class="psm__error">
                  <Icon icon="mdi:alert-circle-outline" width="13" height="13" />
                  {{ coverError }}
                </p>

                <!-- Hidden file input -->
                <input
                  ref="coverFileInput"
                  type="file"
                  accept="image/*"
                  class="psm__file-input"
                  @change="onCoverFileSelected"
                />
              </div>

            </template>

            <!-- ── Export ─────────────────────────────────────────────── -->
            <template v-else-if="activeSection === 'export'">
              <div class="psm__section">
                <p class="psm__section-title">{{ t('pageSettings.exportTitle') }}</p>
                <div class="psm__row">
                  <div class="psm__row-label">
                    <span class="psm__row-name">{{ t('pageSettings.exportAsPdf') }}</span>
                    <span class="psm__row-hint">{{ t('pageSettings.exportAsPdfHint') }}</span>
                  </div>
                  <button class="psm__btn" @click="exportAsPdf">
                    <Icon icon="mdi:file-pdf-box" width="14" height="14" />
                    PDF
                  </button>
                </div>
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
.psm__backdrop {
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
.psm {
  display: flex;
  flex-direction: column;
  width: min(640px, 92vw);
  height: min(460px, 80vh);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  overflow: hidden;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.35);
}

/* ── Header ──────────────────────────────────────────────────────────────── */
.psm__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 1rem;
  height: 44px;
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.psm__header-title {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--color-text);
}

.psm__close {
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

.psm__close:hover {
  background: var(--color-hover);
  color: var(--color-text);
}

/* ── Body ────────────────────────────────────────────────────────────────── */
.psm__body {
  display: flex;
  flex: 1;
  overflow: hidden;
}

/* ── Sidebar ─────────────────────────────────────────────────────────────── */
.psm__sidebar {
  width: 180px;
  flex-shrink: 0;
  border-right: 1px solid var(--color-border);
  padding: 0.5rem 0.375rem;
  overflow-y: auto;
  background: var(--color-sidebar-bg, var(--color-surface));
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.psm__nav-item {
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

.psm__nav-item:hover {
  background: var(--color-hover);
  color: var(--color-text);
}

.psm__nav-item--active {
  background: var(--color-active);
  color: var(--color-text);
  font-weight: 500;
}

.psm__nav-icon {
  flex-shrink: 0;
  opacity: 0.75;
}

.psm__nav-item--active .psm__nav-icon {
  opacity: 1;
}

/* ── Content pane ────────────────────────────────────────────────────────── */
.psm__content {
  flex: 1;
  overflow-y: auto;
  padding: 1.25rem 1.5rem;
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

/* ── Section ─────────────────────────────────────────────────────────────── */
.psm__section {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.psm__section-title {
  font-size: 0.72rem;
  font-weight: 600;
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin: 0;
}

/* ── Row (label + control) ───────────────────────────────────────────────── */
.psm__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.psm__row-label {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.psm__row-name {
  font-size: 0.875rem;
  color: var(--color-text);
}

.psm__row-hint {
  font-size: 0.775rem;
  color: var(--color-text-muted);
  line-height: 1.4;
}

/* ── Toggle switch ───────────────────────────────────────────────────────── */
.psm__toggle {
  position: relative;
  width: 38px;
  height: 22px;
  border-radius: 11px;
  border: none;
  background: var(--color-border);
  cursor: pointer;
  flex-shrink: 0;
  transition: background 0.18s;
  padding: 0;
}

.psm__toggle--on {
  background: var(--color-accent);
}

.psm__toggle-thumb {
  position: absolute;
  top: 3px;
  left: 3px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: #fff;
  transition: transform 0.18s;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
}

.psm__toggle--on .psm__toggle-thumb {
  transform: translateX(16px);
}

/* ── Cover preview ───────────────────────────────────────────────────────── */
.psm__cover-preview {
  width: 100%;
  height: 100px;
  border-radius: 7px;
  overflow: hidden;
  border: 1px solid var(--color-border);
}

.psm__cover-image {
  width: 100%;
  height: 100%;
  background-size: cover;
  background-position: center;
}

/* ── Cover action buttons ────────────────────────────────────────────────── */
.psm__cover-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.psm__btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: transparent;
  font-size: 0.8125rem;
  color: var(--color-text);
  cursor: pointer;
  transition: background 0.1s, border-color 0.1s;
}

.psm__btn:hover:not(:disabled) {
  background: var(--color-hover);
  border-color: var(--color-accent);
}

.psm__btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.psm__btn--danger {
  color: #e05353;
  border-color: var(--color-border);
}

.psm__btn--danger:hover:not(:disabled) {
  background: rgba(224, 83, 83, 0.07);
  border-color: #e05353;
}

/* ── Error text ──────────────────────────────────────────────────────────── */
.psm__error {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 0.8rem;
  color: #e05353;
  margin: 0;
}

/* ── Hidden file input ───────────────────────────────────────────────────── */
.psm__file-input {
  display: none;
}

/* ── Spinner animation ───────────────────────────────────────────────────── */
.spin {
  animation: psm-spin 0.7s linear infinite;
}

@keyframes psm-spin {
  to { transform: rotate(360deg); }
}
</style>

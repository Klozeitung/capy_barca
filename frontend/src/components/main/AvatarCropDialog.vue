<script setup lang="ts">
/**
 * AvatarCropDialog
 *
 * Lets the user choose the 3:4 portrait section of an image that becomes a
 * page's profile picture. The image lies behind a viewport of that shape;
 * dragging moves it, the mouse wheel and the slider zoom it. The image
 * always covers the whole viewport (see ``avatarCropHelpers``), so the
 * result has no empty edges.
 *
 * Confirming draws the visible section onto a canvas of ``AVATAR_OUTPUT``
 * pixels and emits it as WebP, or as PNG where the browser cannot encode
 * WebP. The original image is not uploaded.
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Icon } from '@iconify/vue'
import {
  AVATAR_MAX_ZOOM,
  AVATAR_OUTPUT,
  centredOffset,
  clampOffset,
  coverScale,
  sourceRect,
  zoomAround,
  type Point,
} from './avatarCropHelpers'

const props = defineProps<{ file: File }>()

const emit = defineEmits<{
  (e: 'cancel'): void
  (e: 'confirm', image: Blob, filename: string): void
}>()

const { t } = useI18n()

/** Size of the on-screen viewport in pixels, 3:4 like the result. */
const VIEW = { width: 180, height: 240 }

const objectUrl = URL.createObjectURL(props.file)
const image = ref<HTMLImageElement | null>(null)
const naturalWidth = ref(0)
const naturalHeight = ref(0)
const loadFailed = ref(false)
const working = ref(false)

const zoom = ref(1)
const offset = ref<Point>({ x: 0, y: 0 })

const baseScale = computed(() => coverScale(naturalWidth.value, naturalHeight.value, VIEW))
const scale = computed(() => baseScale.value * zoom.value)

const imageStyle = computed(() => ({
  width: `${naturalWidth.value * scale.value}px`,
  height: `${naturalHeight.value * scale.value}px`,
  transform: `translate(${offset.value.x}px, ${offset.value.y}px)`,
}))

function onImageLoad(e: Event): void {
  const img = e.target as HTMLImageElement
  image.value = img
  naturalWidth.value = img.naturalWidth
  naturalHeight.value = img.naturalHeight
  zoom.value = 1
  offset.value = centredOffset(img.naturalWidth, img.naturalHeight, baseScale.value, VIEW)
}

function onImageError(): void {
  loadFailed.value = true
}

// ── Zoom ──────────────────────────────────────────────────────────────────────

function setZoom(next: number, anchor: Point = { x: VIEW.width / 2, y: VIEW.height / 2 }): void {
  const bounded = Math.min(AVATAR_MAX_ZOOM, Math.max(1, next))
  const oldScale = scale.value
  zoom.value = bounded
  offset.value = zoomAround(
    offset.value, oldScale, scale.value, anchor,
    naturalWidth.value, naturalHeight.value, VIEW,
  )
}

function onSlider(e: Event): void {
  setZoom(Number((e.target as HTMLInputElement).value))
}

function onWheel(e: WheelEvent): void {
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
  const anchor = { x: e.clientX - rect.left, y: e.clientY - rect.top }
  setZoom(zoom.value * (e.deltaY < 0 ? 1.1 : 1 / 1.1), anchor)
}

// ── Drag ──────────────────────────────────────────────────────────────────────

let dragStart: { pointer: Point; offset: Point } | null = null

function onPointerDown(e: PointerEvent): void {
  if (!image.value) return
  ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
  dragStart = { pointer: { x: e.clientX, y: e.clientY }, offset: { ...offset.value } }
}

function onPointerMove(e: PointerEvent): void {
  if (!dragStart) return
  offset.value = clampOffset(
    {
      x: dragStart.offset.x + e.clientX - dragStart.pointer.x,
      y: dragStart.offset.y + e.clientY - dragStart.pointer.y,
    },
    naturalWidth.value, naturalHeight.value, scale.value, VIEW,
  )
}

function onPointerUp(): void {
  dragStart = null
}

// ── Result ────────────────────────────────────────────────────────────────────

function encode(canvas: HTMLCanvasElement, type: string): Promise<Blob | null> {
  return new Promise(resolve => canvas.toBlob(resolve, type, 0.9))
}

async function confirm(): Promise<void> {
  if (!image.value || working.value) return
  working.value = true
  try {
    const canvas = document.createElement('canvas')
    canvas.width = AVATAR_OUTPUT.width
    canvas.height = AVATAR_OUTPUT.height
    const context = canvas.getContext('2d')
    if (!context) {
      loadFailed.value = true
      return
    }
    const src = sourceRect(offset.value, scale.value, VIEW)
    context.imageSmoothingQuality = 'high'
    context.drawImage(
      image.value, src.x, src.y, src.width, src.height,
      0, 0, AVATAR_OUTPUT.width, AVATAR_OUTPUT.height,
    )
    // A browser without a WebP encoder silently returns PNG for the request.
    const webp = await encode(canvas, 'image/webp')
    if (webp && webp.type === 'image/webp') {
      emit('confirm', webp, 'avatar.webp')
      return
    }
    const png = await encode(canvas, 'image/png')
    if (png) emit('confirm', png, 'avatar.png')
    else loadFailed.value = true
  } finally {
    working.value = false
  }
}

// ── Lifecycle ─────────────────────────────────────────────────────────────────

function onKeydown(e: KeyboardEvent): void {
  if (e.key === 'Escape') emit('cancel')
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  URL.revokeObjectURL(objectUrl)
})

function onBackdropClick(e: MouseEvent): void {
  if ((e.target as HTMLElement).classList.contains('acd__backdrop')) emit('cancel')
}
</script>

<template>
  <Teleport to="body">
    <div class="acd__backdrop" @click="onBackdropClick">
      <div class="acd" role="dialog" aria-modal="true" :aria-label="t('pageSettings.avatarCropTitle')">
        <div class="acd__header">
          <span class="acd__title">{{ t('pageSettings.avatarCropTitle') }}</span>
          <button class="acd__close" :aria-label="t('actions.cancel')" @click="emit('cancel')">
            <Icon icon="mdi:close" width="15" height="15" />
          </button>
        </div>

        <p v-if="loadFailed" class="acd__error">
          <Icon icon="mdi:alert-circle-outline" width="13" height="13" />
          {{ t('pageSettings.avatarLoadError') }}
        </p>

        <template v-else>
          <div
            class="acd__viewport"
            :style="{ width: `${VIEW.width}px`, height: `${VIEW.height}px` }"
            @pointerdown="onPointerDown"
            @pointermove="onPointerMove"
            @pointerup="onPointerUp"
            @pointercancel="onPointerUp"
            @wheel.prevent="onWheel"
          >
            <img
              :src="objectUrl"
              class="acd__image"
              :style="imageStyle"
              alt=""
              draggable="false"
              @load="onImageLoad"
              @error="onImageError"
            />
            <div class="acd__mask" />
          </div>

          <label class="acd__zoom">
            <Icon icon="mdi:magnify-minus-outline" width="14" height="14" />
            <input
              type="range"
              min="1"
              :max="AVATAR_MAX_ZOOM"
              step="0.01"
              :value="zoom"
              :aria-label="t('pageSettings.avatarCropZoom')"
              @input="onSlider"
            />
            <Icon icon="mdi:magnify-plus-outline" width="14" height="14" />
          </label>

          <p class="acd__hint">{{ t('pageSettings.avatarCropHint') }}</p>
        </template>

        <div class="acd__footer">
          <button class="acd__btn" @click="emit('cancel')">{{ t('actions.cancel') }}</button>
          <button
            class="acd__btn acd__btn--primary"
            :disabled="!image || loadFailed || working"
            @click="confirm"
          >
            <Icon v-if="working" icon="mdi:loading" width="14" height="14" class="acd__spin" />
            {{ t('pageSettings.avatarCropApply') }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.acd__backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1100;
}

.acd {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 0 1.25rem 1rem;
  width: min(340px, 92vw);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.35);
}

.acd__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  align-self: stretch;
  height: 44px;
  border-bottom: 1px solid var(--color-border);
  margin: 0 -1.25rem;
  padding: 0 1rem;
}

.acd__title {
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--color-text);
}

.acd__close {
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
}

.acd__close:hover { background: var(--color-hover); color: var(--color-text); }

.acd__viewport {
  position: relative;
  overflow: hidden;
  border-radius: 6px;
  background: var(--color-hover);
  cursor: grab;
  touch-action: none;
  user-select: none;
}

.acd__viewport:active { cursor: grabbing; }

.acd__image {
  position: absolute;
  top: 0;
  left: 0;
  max-width: none;
  transform-origin: 0 0;
  pointer-events: none;
}

/* Outlines the section that becomes the picture, with its rounded corners. */
.acd__mask {
  position: absolute;
  inset: 0;
  border-radius: 4px;
  box-shadow: 0 0 0 9999px rgba(0, 0, 0, 0.5);
  pointer-events: none;
}

.acd__zoom {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--color-text-muted);
}

.acd__zoom input { width: 180px; }

.acd__hint {
  margin: 0;
  font-size: 0.775rem;
  color: var(--color-text-muted);
  text-align: center;
}

.acd__error {
  display: flex;
  align-items: center;
  gap: 5px;
  margin: 1rem 0 0;
  font-size: 0.8rem;
  color: #e05353;
}

.acd__footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  align-self: stretch;
}

.acd__btn {
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
}

.acd__btn:hover:not(:disabled) { background: var(--color-hover); }

.acd__btn:disabled { opacity: 0.5; cursor: not-allowed; }

.acd__btn--primary {
  background: var(--color-accent);
  border-color: var(--color-accent);
  color: #fff;
}

.acd__btn--primary:hover:not(:disabled) { background: var(--color-accent); filter: brightness(1.05); }

.acd__spin { animation: acd-spin 0.7s linear infinite; }

@keyframes acd-spin { to { transform: rotate(360deg); } }
</style>

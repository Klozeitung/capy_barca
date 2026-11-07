<script setup lang="ts">
/**
 * FamilyTreeView
 *
 * The family tree as a database view type, drawn as SVG.
 *
 * The server computes the tree around the root set in the view settings
 * (``useFamilyTree``): who belongs to it, the generation of each person,
 * every link with its periods. This component picks what holds at the
 * instant of the view (``familyTreeAt.ts``, the overall view without one),
 * lays the rows out (``familyTreeLayout.ts``) and draws them. Nothing is
 * cached: the tree is loaded whenever the view is opened, its settings
 * change, or the parent reports changed entries (``reload``).
 *
 * Nodes
 * -----
 * Profile picture (a question mark without one), title, life span in the
 * user's date format and the property line under it. People who died by
 * the instant are greyed out; people without a date of birth carry "date
 * unknown" at an instant. Warnings of the server (two biological mothers or
 * fathers) show as a mark with a tooltip. A click opens the entry in the
 * side view; virtual parents of siblings without a shared parent are not
 * entries and do nothing.
 *
 * Links
 * -----
 * Parent links run from the parent's picture across above the child's row;
 * partnerships connect the pictures. The style follows the nuance
 * (``parentStyle``, ``partnerStyle``). A link that closes a cycle of
 * parents is drawn red and dashed. Overlays are drawn in their colour with
 * arrows as configured and, at an instant, their nuance where asked for.
 *
 * ``exportSvg`` downloads the drawing as shown (``familyTreeExport.ts``).
 */
import { computed, onMounted, ref, useId, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useAuthStore } from '@/stores/auth'
import type { DatabaseView } from '@/stores/database'
import { fetchFamilyTree, type FamilyTree, type TreeToken } from '@/composables/useFamilyTree'
import { formatDateString } from './properties/cells/cellUtils'
import { toMinute, treeAt, type NodeAt } from './familyTreeAt'
import {
  AVATAR_SIZE,
  LINE_HEIGHT,
  VIRTUAL_SIZE,
  directPath,
  layoutTree,
  overlayColor,
  parentPath,
  parentStyle,
  partnerStyle,
  pathMiddle,
  wrapText,
  type Box,
  type LinkStyle,
} from './familyTreeLayout'
import { downloadSvg, standaloneSvg, svgFileName } from './familyTreeExport'

const props = defineProps<{
  databaseId: string
  view: DatabaseView
  /** The name the view is shown under, for the export's file name. */
  viewName: string
}>()

const emit = defineEmits<{
  (e: 'open-entry', entryId: string): void
}>()

const { t } = useI18n()
const auth = useAuthStore()

const PADDING = 24

// Ids of the SVG definitions, unique per view on the page.
const uid = `ft${(useId() ?? '').replace(/[^a-zA-Z0-9-]/g, '')}`

const tree = ref<FamilyTree | null>(null)
const loading = ref(false)
const failed = ref(false)
const svgEl = ref<SVGSVGElement | null>(null)

let requestCounter = 0

async function reload(): Promise<void> {
  const request = ++requestCounter
  loading.value = true
  failed.value = false
  try {
    const result = await fetchFamilyTree(props.databaseId, props.view.id)
    if (request === requestCounter) tree.value = result
  } catch {
    if (request === requestCounter) failed.value = true
  } finally {
    if (request === requestCounter) loading.value = false
  }
}

onMounted(reload)

// The instant only changes what is shown; everything else changes the tree.
watch(
  () => [
    props.view.id,
    props.view.familyTreeRootId,
    props.view.familyTreeScope,
    JSON.stringify(props.view.familyTreeProperties ?? []),
    JSON.stringify(props.view.familyTreeOverlays ?? []),
  ],
  reload,
)

const at = computed(() => toMinute(props.view.familyTreeAt ?? null))
const layout = computed(() => (tree.value ? layoutTree(tree.value) : null))
const state = computed(() => (tree.value ? treeAt(tree.value, at.value) : null))

const boxes = computed(() => layout.value?.boxes ?? new Map<string, Box>())
const width = computed(() => (layout.value?.width ?? 0) + PADDING * 2)
const height = computed(() => (layout.value?.height ?? 0) + PADDING * 2)

function box(id: string): Box | undefined {
  return boxes.value.get(id)
}

// ── Text of a node ────────────────────────────────────────────────────────────

function formatDate(raw: string | null): string {
  if (!raw) return ''
  return formatDateString(raw, auth.dateFormat, raw.includes('T'))
}

/** The life span: "birth – death", "? – death", "birth –". */
function lifespan(item: NodeAt): string {
  const birth = formatDate(item.node.birth_value)
  const death = formatDate(item.node.death_value)
  if (!birth && !death) return ''
  return `${birth || '?'} – ${death}`.trimEnd()
}

function tokenText(token: TreeToken): string {
  if ('text' in token) return token.text
  if ('date' in token) {
    const start = formatDate(token.date)
    return token.end ? `${start} – ${formatDate(token.end)}` : start
  }
  return token.check ? t('db.familyTree.yes') : t('db.familyTree.no')
}

/** The property line: every value, comma separated, never shortened. */
function propertyLines(item: NodeAt): string[] {
  const text = item.properties.flatMap((p) => p.tokens.map(tokenText)).filter(Boolean).join(', ')
  return text ? wrapText(text) : []
}

function titleLines(item: NodeAt): string[] {
  return wrapText(item.node.title || t('main.untitled'))
}

function warningText(item: NodeAt): string {
  return item.node.warnings.map((w) => t(`db.familyTree.warning.${w}`)).join('\n')
}

// ── Links ─────────────────────────────────────────────────────────────────────

interface DrawnLink {
  key: string
  path: string
  style: LinkStyle
  broken: boolean
  title: string
}

const parentLinks = computed<DrawnLink[]>(() => {
  const result: DrawnLink[] = []
  for (const link of state.value?.parents ?? []) {
    const parent = box(link.parent)
    const child = box(link.child)
    if (!parent || !child) continue
    result.push({
      key: `p:${link.parent}:${link.child}`,
      path: parentPath(parent, child),
      style: parentStyle(link.base),
      broken: link.broken,
      title: link.broken ? t('db.familyTree.brokenLink') : link.label ?? t('db.familyTree.unspecified'),
    })
  }
  return result
})

const partnerLinks = computed<DrawnLink[]>(() => {
  const result: DrawnLink[] = []
  for (const link of state.value?.partners ?? []) {
    const a = box(link.a)
    const b = box(link.b)
    if (!a || !b) continue
    result.push({
      key: `r:${link.a}:${link.b}`,
      path: directPath(a, b),
      style: partnerStyle(link.base),
      broken: false,
      title: link.label ?? t('db.familyTree.unspecified'),
    })
  }
  return result
})

const virtualLinks = computed(() =>
  (state.value?.virtual ?? []).flatMap((link) => {
    const parent = box(link.parent)
    const child = box(link.child)
    return parent && child ? [{ key: `v:${link.parent}:${link.child}`, path: parentPath(parent, child) }] : []
  }),
)

const overlayLinks = computed(() =>
  (state.value?.overlays ?? []).flatMap((link, index) => {
    const source = box(link.source)
    const target = box(link.target)
    const overlay = tree.value?.overlays[link.overlay]
    if (!source || !target || !overlay) return []
    const path = directPath(source, target)
    return [{
      key: `o:${index}`,
      path,
      color: overlayColor(overlay.color),
      markerEnd: overlay.direction !== 'none' ? `url(#${uid}-arrow-${link.overlay})` : undefined,
      markerStart: overlay.direction === 'both' ? `url(#${uid}-arrow-start-${link.overlay})` : undefined,
      label: link.label,
      middle: pathMiddle(path),
      title: overlay.name ?? '',
    }]
  }),
)

const overlayMarkers = computed(() =>
  (tree.value?.overlays ?? []).map((overlay, index) => ({ index, color: overlayColor(overlay.color) })),
)

function markMiddle(link: DrawnLink): { x: number; y: number } {
  return pathMiddle(link.path)
}

// ── Export ────────────────────────────────────────────────────────────────────

async function exportSvg(): Promise<void> {
  if (!svgEl.value) return
  const content = await standaloneSvg(svgEl.value)
  downloadSvg(content, svgFileName(props.viewName))
}

defineExpose({ reload, exportSvg })
</script>

<template>
  <div class="ft-view">
    <p v-if="!view.familyTreeRootId" class="ft-view__hint">{{ t('db.familyTree.noRoot') }}</p>
    <p v-else-if="failed" class="ft-view__hint">{{ t('db.familyTree.loadError') }}</p>
    <p v-else-if="tree && !tree.root_valid" class="ft-view__hint">{{ t('db.familyTree.rootMissing') }}</p>
    <p v-else-if="!tree && loading" class="ft-view__hint">{{ t('db.familyTree.loading') }}</p>

    <div v-if="tree && tree.root_valid && state" class="ft-view__scroll">
      <svg
        ref="svgEl"
        class="ft-view__svg"
        :width="width"
        :height="height"
        :viewBox="`0 0 ${width} ${height}`"
        role="img"
        :aria-label="viewName"
      >
        <defs>
          <filter :id="`${uid}-grey`">
            <feColorMatrix type="saturate" values="0" />
            <feComponentTransfer>
              <feFuncA type="linear" slope="0.55" />
            </feComponentTransfer>
          </filter>
          <template v-for="marker in overlayMarkers" :key="marker.index">
            <marker
              :id="`${uid}-arrow-${marker.index}`"
              viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" :fill="marker.color" />
            </marker>
            <marker
              :id="`${uid}-arrow-start-${marker.index}`"
              viewBox="0 0 10 10" refX="1" refY="5" markerWidth="7" markerHeight="7" orient="auto"
            >
              <path d="M 10 0 L 0 5 L 10 10 z" :fill="marker.color" />
            </marker>
          </template>
          <clipPath :id="`${uid}-picture`">
            <circle :cx="AVATAR_SIZE / 2" :cy="AVATAR_SIZE / 2" :r="AVATAR_SIZE / 2" />
          </clipPath>
        </defs>

        <g :transform="`translate(${PADDING} ${PADDING})`">
          <!-- Links of virtual parents -->
          <path
            v-for="link in virtualLinks"
            :key="link.key"
            :d="link.path"
            class="ft-link ft-link--virtual"
          />

          <!-- Parent links -->
          <g v-for="link in parentLinks" :key="link.key">
            <path
              :d="link.path"
              class="ft-link"
              :class="{ 'ft-link--muted': link.style.muted, 'ft-link--broken': link.broken }"
              :stroke-width="link.style.width"
              :stroke-dasharray="link.broken ? '6 3' : link.style.dash ?? undefined"
            >
              <title>{{ link.title }}</title>
            </path>
            <circle
              v-if="link.style.marked"
              :cx="markMiddle(link).x" :cy="markMiddle(link).y" r="3.5"
              class="ft-link-mark"
            />
          </g>

          <!-- Partnerships -->
          <g v-for="link in partnerLinks" :key="link.key">
            <path
              :d="link.path"
              class="ft-link"
              :class="{ 'ft-link--muted': link.style.muted }"
              :stroke-width="link.style.width"
              :stroke-dasharray="link.style.dash ?? undefined"
            >
              <title>{{ link.title }}</title>
            </path>
            <circle
              v-if="link.style.marked"
              :cx="markMiddle(link).x" :cy="markMiddle(link).y" r="3.5"
              class="ft-link-mark"
            />
          </g>

          <!-- Overlays -->
          <g v-for="link in overlayLinks" :key="link.key">
            <path
              :d="link.path"
              class="ft-overlay"
              :stroke="link.color"
              :marker-end="link.markerEnd"
              :marker-start="link.markerStart"
            >
              <title>{{ link.title }}</title>
            </path>
            <text
              v-if="link.label"
              :x="link.middle.x" :y="link.middle.y - 4"
              class="ft-overlay-label"
              :fill="link.color"
            >{{ link.label }}</text>
          </g>

          <!-- Nodes -->
          <g
            v-for="item in state.nodes"
            :key="item.node.id"
            :transform="`translate(${box(item.node.id)?.x ?? 0} ${box(item.node.id)?.y ?? 0})`"
            class="ft-node"
            :class="{ 'ft-node--virtual': item.node.virtual, 'ft-node--root': item.node.id === tree.root }"
            @click="!item.node.virtual && emit('open-entry', item.node.id)"
          >
            <template v-if="item.node.virtual">
              <circle :cx="VIRTUAL_SIZE / 2" :cy="VIRTUAL_SIZE / 2" :r="VIRTUAL_SIZE / 2" class="ft-node__placeholder" />
              <text :x="VIRTUAL_SIZE / 2" :y="VIRTUAL_SIZE / 2 + 5" class="ft-node__question">?</text>
              <title>{{ t('db.familyTree.unknownParent') }}</title>
            </template>
            <template v-else>
              <title>{{ item.node.title || t('main.untitled') }}</title>
              <g
                :transform="`translate(${((box(item.node.id)?.width ?? 0) - AVATAR_SIZE) / 2} 0)`"
                :filter="item.deceased ? `url(#${uid}-grey)` : undefined"
              >
                <circle :cx="AVATAR_SIZE / 2" :cy="AVATAR_SIZE / 2" :r="AVATAR_SIZE / 2" class="ft-node__placeholder" />
                <image
                  v-if="item.node.avatar"
                  :href="item.node.avatar"
                  :width="AVATAR_SIZE" :height="AVATAR_SIZE"
                  :clip-path="`url(#${uid}-picture)`"
                  preserveAspectRatio="xMidYMid slice"
                />
                <text v-else :x="AVATAR_SIZE / 2" :y="AVATAR_SIZE / 2 + 7" class="ft-node__question ft-node__question--large">?</text>
              </g>
              <g v-if="item.node.warnings.length" class="ft-node__warning">
                <circle :cx="(box(item.node.id)?.width ?? 0) / 2 + AVATAR_SIZE / 2" cy="8" r="8" />
                <text :x="(box(item.node.id)?.width ?? 0) / 2 + AVATAR_SIZE / 2" y="12">!</text>
                <title>{{ warningText(item) }}</title>
              </g>
              <text :x="(box(item.node.id)?.width ?? 0) / 2" :y="AVATAR_SIZE + 6" class="ft-node__text">
                <tspan
                  v-for="(line, i) in titleLines(item)"
                  :key="`t${i}`"
                  :x="(box(item.node.id)?.width ?? 0) / 2"
                  :dy="LINE_HEIGHT"
                  class="ft-node__title"
                >{{ line }}</tspan>
                <tspan
                  :x="(box(item.node.id)?.width ?? 0) / 2"
                  :dy="LINE_HEIGHT"
                  class="ft-node__muted"
                >{{ item.dateUnknown ? t('db.familyTree.dateUnknown') : lifespan(item) }}</tspan>
                <tspan
                  v-for="(line, i) in propertyLines(item)"
                  :key="`p${i}`"
                  :x="(box(item.node.id)?.width ?? 0) / 2"
                  :dy="LINE_HEIGHT"
                  class="ft-node__muted"
                >{{ line }}</tspan>
              </text>
            </template>
          </g>
        </g>
      </svg>
    </div>
  </div>
</template>

<style scoped>
.ft-view {
  padding: 8px 0;
}

.ft-view__hint {
  margin: 24px 0;
  text-align: center;
  font-size: 0.875rem;
  color: var(--color-text-muted);
}

.ft-view__scroll {
  overflow: auto;
  max-height: 80vh;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-bg);
}

.ft-view__svg {
  display: block;
  font-family: inherit;
}

.ft-link {
  fill: none;
  stroke: var(--color-text);
  stroke-linecap: round;
  opacity: 0.75;
}

.ft-link--muted {
  stroke: var(--color-text-muted);
  opacity: 0.6;
}

.ft-link--broken {
  stroke: #e05353;
  opacity: 1;
}

.ft-link--virtual {
  stroke: var(--color-text-muted);
  stroke-dasharray: 2 4;
  stroke-width: 1;
}

.ft-link-mark {
  fill: var(--color-bg);
  stroke: var(--color-text);
  stroke-width: 1.5;
}

.ft-overlay {
  fill: none;
  stroke-width: 1.5;
  opacity: 0.9;
}

.ft-overlay-label {
  font-size: 10px;
  text-anchor: middle;
}

.ft-node {
  cursor: pointer;
}

.ft-node--virtual {
  cursor: default;
}

.ft-node__placeholder {
  fill: var(--color-surface);
  stroke: var(--color-border);
  stroke-width: 1;
}

.ft-node--root .ft-node__placeholder {
  stroke: var(--color-accent);
  stroke-width: 2;
}

.ft-node__question {
  fill: var(--color-text-muted);
  font-size: 13px;
  text-anchor: middle;
}

.ft-node__question--large {
  font-size: 22px;
}

.ft-node__text {
  font-size: 12px;
  text-anchor: middle;
  fill: var(--color-text);
}

.ft-node__title {
  font-weight: 600;
}

.ft-node__muted {
  fill: var(--color-text-muted);
  font-size: 11px;
}

.ft-node__warning circle {
  fill: #e0a030;
}

.ft-node__warning text {
  fill: #fff;
  font-size: 12px;
  font-weight: 700;
  text-anchor: middle;
}

.ft-node:hover .ft-node__title {
  text-decoration: underline;
}
</style>

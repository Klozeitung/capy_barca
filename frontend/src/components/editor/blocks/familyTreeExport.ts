/**
 * familyTreeExport
 *
 * The family tree as a standalone SVG file, as the view shows it: at the
 * instant set in the view settings, with its overlays.
 *
 * The drawing in the page takes its colours and fonts from the stylesheet
 * (theme variables) and its profile pictures from the server. A file opened
 * elsewhere has neither, so the export copies the computed style into
 * attributes and embeds every picture as a data URL. A picture that cannot
 * be loaded is left out; its node keeps the placeholder.
 *
 * To keep the file small, the root carries every style property and each
 * element below only those that differ from its parent, since SVG inherits
 * them; opacity, which multiplies instead, stays wherever it is not 1.
 * Classes and the attributes Vue adds for scoped styles are dropped.
 */
import { apiClient } from '@/api/client'

/** Style properties copied from the page into the file. */
export const INLINED_PROPERTIES = [
  'fill',
  'fill-opacity',
  'stroke',
  'stroke-width',
  'stroke-dasharray',
  'stroke-linecap',
  'stroke-opacity',
  'opacity',
  'font-family',
  'font-size',
  'font-weight',
  'font-style',
  'text-anchor',
  'dominant-baseline',
] as const

export interface ExportDeps {
  /** The computed style of an element of the page. */
  style: (element: Element) => Pick<CSSStyleDeclaration, 'getPropertyValue'>
  /** A picture as a data URL, or null when it cannot be loaded. */
  image: (url: string) => Promise<string | null>
}

function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(blob)
  })
}

export const browserDeps: ExportDeps = {
  style: (element) => window.getComputedStyle(element),
  image: async (url) => {
    if (url.startsWith('data:')) return url
    try {
      return await blobToDataUrl(await apiClient.getBlob(url))
    } catch {
      return null
    }
  },
}

/**
 * *svg* as the text of a standalone SVG file: styles inlined, pictures
 * embedded, elements marked ``data-export="skip"`` left out.
 */
export async function standaloneSvg(svg: SVGSVGElement, deps: ExportDeps = browserDeps): Promise<string> {
  const copy = svg.cloneNode(true) as SVGSVGElement
  const originals = [svg, ...Array.from(svg.querySelectorAll('*'))]
  const copies = [copy, ...Array.from(copy.querySelectorAll('*'))]
  const computed = new Map<Element, Map<string, string>>()
  originals.forEach((original, index) => {
    const target = copies[index]
    const style = deps.style(original)
    const values = new Map<string, string>()
    const inherited = original === svg ? undefined : computed.get(original.parentElement as Element)
    for (const property of INLINED_PROPERTIES) {
      const value = style.getPropertyValue(property).trim()
      if (!value) continue
      values.set(property, value)
      // Opacity is not inherited but multiplied, so it goes on every element that has one.
      const needed = property === 'opacity' ? value !== '1' : inherited?.get(property) !== value
      if (needed || original === svg) target.setAttribute(property, value)
    }
    computed.set(original, values)
    target.removeAttribute('class')
    for (const name of target.getAttributeNames()) {
      if (name.startsWith('data-v-')) target.removeAttribute(name)
    }
  })
  for (const skipped of Array.from(copy.querySelectorAll('[data-export="skip"]'))) skipped.remove()

  const images = Array.from(copy.querySelectorAll('image'))
  await Promise.all(images.map(async (image) => {
    const url = image.getAttribute('href') ?? image.getAttribute('xlink:href')
    const data = url ? await deps.image(url) : null
    if (data) image.setAttribute('href', data)
    else image.remove()
  }))

  copy.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
  copy.setAttribute('xmlns:xlink', 'http://www.w3.org/1999/xlink')
  return `<?xml version="1.0" encoding="UTF-8"?>\n${new XMLSerializer().serializeToString(copy)}`
}

/** A file name for the export of a view called *name*. */
export function svgFileName(name: string): string {
  const cleaned = name.replace(/[\\/:*?"<>|]+/g, '').trim()
  return `${cleaned || 'family-tree'}.svg`
}

/** Offer *content* as a download named *filename*. */
export function downloadSvg(content: string, filename: string): void {
  const url = URL.createObjectURL(new Blob([content], { type: 'image/svg+xml' }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

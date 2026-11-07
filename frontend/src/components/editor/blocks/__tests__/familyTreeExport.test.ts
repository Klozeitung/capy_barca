import { describe, it, expect } from 'vitest'
import { standaloneSvg, svgFileName, type ExportDeps } from '../familyTreeExport'

function svg(): SVGSVGElement {
  const container = document.createElement('div')
  container.innerHTML = `
    <svg viewBox="0 0 10 10">
      <line class="ft-line" x1="0" y1="0" x2="1" y2="1" />
      <image href="/static/uploads/avatars/a.webp" />
      <image href="/static/uploads/avatars/missing.webp" />
      <rect data-export="skip" />
    </svg>`
  return container.querySelector('svg') as SVGSVGElement
}

const deps: ExportDeps = {
  style: (element) => ({
    getPropertyValue: (property: string) =>
      element.tagName.toLowerCase() === 'line' && property === 'stroke' ? 'rgb(1, 2, 3)' : '',
  }),
  image: async (url) => (url.includes('missing') ? null : 'data:image/webp;base64,AAAA'),
}

describe('standaloneSvg', () => {
  it('inlines styles, embeds pictures and drops what is not for export', async () => {
    const text = await standaloneSvg(svg(), deps)
    expect(text.startsWith('<?xml')).toBe(true)
    expect(text).toContain('stroke="rgb(1, 2, 3)"')
    expect(text).not.toContain('class="ft-line"')
    expect(text).toContain('data:image/webp;base64,AAAA')
    expect(text).not.toContain('missing.webp')
    expect(text).not.toContain('data-export')
    expect(text).toContain('xmlns="http://www.w3.org/2000/svg"')
  })
})

describe('svgFileName', () => {
  it('names the file after the view without characters a file system refuses', () => {
    expect(svgFileName('Tree: A/B')).toBe('Tree AB.svg')
    expect(svgFileName('  ')).toBe('family-tree.svg')
  })
})

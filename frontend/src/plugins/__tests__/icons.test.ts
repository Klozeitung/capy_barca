import { describe, it, expect, beforeAll } from 'vitest'
import { getIcon, iconLoaded, loadIcon } from '@iconify/vue'
import {
  ICON_SETS,
  customIconData,
  isCustomIcon,
  registerCustomIcon,
  registerIconSets,
  setCustomIconSource,
} from '@/plugins/icons'

describe('registerIconSets', () => {
  let registered: string[] = []

  beforeAll(() => {
    registered = registerIconSets()
  })

  it('registers every bundled set', () => {
    expect(registered).toEqual(['mdi', 'tabler', 'lucide'])
    expect(ICON_SETS.map((set) => set.prefix)).toEqual(registered)
  })

  it('makes icons of every set available without a network request', () => {
    for (const set of ICON_SETS) {
      const name = Object.keys(set.icons)[0]
      expect(iconLoaded(`${set.prefix}:${name}`)).toBe(true)
    }
  })

  it('serves icon data synchronously, as the editor row chips need it', () => {
    const data = getIcon('mdi:star-outline')
    expect(data).not.toBeNull()
    expect(data?.body).toContain('<path')
  })

  it('leaves names outside the bundled sets unloaded', () => {
    expect(iconLoaded('unknown-set:anything')).toBe(false)
  })
})

describe('uploaded icons', () => {
  const ID_A = '3f2a1b4c-5d6e-4f70-8a9b-0c1d2e3f4a5b'
  const ID_B = '4a3b2c1d-6e5f-4a07-9b8a-1c0d2e3f4a5b'

  it('tells uploaded icons from bundled ones', () => {
    expect(isCustomIcon(`custom:${ID_A}`)).toBe(true)
    expect(isCustomIcon('mdi:star-outline')).toBe(false)
    expect(isCustomIcon(null)).toBe(false)
    expect(isCustomIcon('')).toBe(false)
  })

  it('draws the uploaded image in a square', () => {
    const data = customIconData('/static/uploads/icons/a.png')
    expect(data.width).toBe(24)
    expect(data.height).toBe(24)
    expect(data.body).toContain('href="/static/uploads/icons/a.png"')
    expect(data.body).toContain('preserveAspectRatio="xMidYMid meet"')
  })

  it('escapes the URL inside the attribute', () => {
    expect(customIconData('/a"b<c>.png').body).toContain('href="/a&quot;b&lt;c&gt;.png"')
  })

  it('makes a registered icon available at once', () => {
    registerCustomIcon(`custom:${ID_A}`, '/static/uploads/icons/a.png')
    expect(iconLoaded(`custom:${ID_A}`)).toBe(true)
    expect(getIcon(`custom:${ID_A}`)?.body).toContain('/static/uploads/icons/a.png')
  })

  it('asks the source for an icon it has not been given', async () => {
    const asked: string[] = []
    setCustomIconSource(async (name) => {
      asked.push(name)
      return name === ID_B ? '/static/uploads/icons/b.webp' : null
    })

    const data = await loadIcon(`custom:${ID_B}`)

    expect(asked).toEqual([ID_B])
    expect(data.body).toContain('/static/uploads/icons/b.webp')
  })
})

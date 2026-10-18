import { describe, it, expect, beforeAll } from 'vitest'
import { getIcon, iconLoaded } from '@iconify/vue'
import { ICON_SETS, registerIconSets } from '@/plugins/icons'

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

import { afterEach, describe, expect, it, vi } from 'vitest'
import en from '@/locales/en.json'
import de from '@/locales/de.json'

vi.mock('@/api/client', () => ({
  apiClient: { get: vi.fn() },
}))

import { apiClient } from '@/api/client'
import {
  filterSyntaxEntries,
  groupSyntaxEntries,
  loadFormulaSyntax,
  resetFormulaSyntaxCache,
  type FormulaSyntax,
  type FormulaSyntaxEntry,
} from '@/composables/formulaSyntax'

function entry(key: string, category: string, insert = ''): FormulaSyntaxEntry {
  return { key, name: key, category, signature: `${key}(x)`, insert }
}

const SYNTAX: FormulaSyntax = {
  categories: ['basics', 'logic', 'math', 'operator'],
  entries: [
    entry('prop', 'basics', "prop('')"),
    entry('if', 'logic', 'if(, , )'),
    entry('round', 'math', 'round(, 0)'),
    entry('abs', 'math', 'abs()'),
  ],
}

const get = vi.mocked(apiClient.get)

afterEach(() => {
  resetFormulaSyntaxCache()
  get.mockReset()
})

describe('loadFormulaSyntax', () => {
  it('loads the reference once and shares a running request', async () => {
    get.mockResolvedValue(SYNTAX)
    const [first, second] = await Promise.all([loadFormulaSyntax(), loadFormulaSyntax()])
    const third = await loadFormulaSyntax()
    expect(first).toBe(SYNTAX)
    expect(second).toBe(SYNTAX)
    expect(third).toBe(SYNTAX)
    expect(get).toHaveBeenCalledTimes(1)
    expect(get).toHaveBeenCalledWith('/api/databases/formula-syntax')
  })

  it('tries again after a failed load', async () => {
    get.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(SYNTAX)
    await expect(loadFormulaSyntax()).rejects.toThrow('offline')
    await expect(loadFormulaSyntax()).resolves.toBe(SYNTAX)
    expect(get).toHaveBeenCalledTimes(2)
  })
})

describe('filterSyntaxEntries', () => {
  const textOf = (e: FormulaSyntaxEntry) => (e.key === 'abs' ? 'Absolute value' : '')

  it('returns every entry for a blank query', () => {
    expect(filterSyntaxEntries(SYNTAX.entries, '   ', textOf)).toEqual(SYNTAX.entries)
  })

  it('matches the name, the signature and the localized text, ignoring case', () => {
    expect(filterSyntaxEntries(SYNTAX.entries, 'ROUND', textOf).map(e => e.key)).toEqual(['round'])
    expect(filterSyntaxEntries(SYNTAX.entries, 'if(x', textOf).map(e => e.key)).toEqual(['if'])
    expect(filterSyntaxEntries(SYNTAX.entries, 'absolute', textOf).map(e => e.key)).toEqual(['abs'])
  })
})

describe('groupSyntaxEntries', () => {
  it('follows the category order and leaves empty categories out', () => {
    const groups = groupSyntaxEntries(SYNTAX, [SYNTAX.entries[3], SYNTAX.entries[0]])
    expect(groups.map(g => g.category)).toEqual(['basics', 'math'])
    expect(groups[1].entries.map(e => e.key)).toEqual(['abs'])
  })
})

describe('formula help locales', () => {
  it('describe the same entries in every language, each with a description and an example', () => {
    const enHelp = en.db.formulaHelp as Record<string, { description: string; example: string }>
    const deHelp = de.db.formulaHelp as Record<string, { description: string; example: string }>
    expect(Object.keys(deHelp).sort()).toEqual(Object.keys(enHelp).sort())
    for (const help of [enHelp, deHelp]) {
      for (const text of Object.values(help)) {
        expect(text.description.trim()).not.toBe('')
        expect(text.example.trim()).not.toBe('')
      }
    }
  })
})

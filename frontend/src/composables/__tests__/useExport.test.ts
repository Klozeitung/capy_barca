import { describe, it, expect } from 'vitest'
import { buildCsvHeader } from '@/composables/useExport'

// ── buildCsvHeader ────────────────────────────────────────────────────────────

describe('buildCsvHeader', () => {
  it('returns the plain name when the description is empty', () => {
    expect(buildCsvHeader('Status', '', 'Description')).toBe('Status')
  })

  it('returns the plain name when the description is whitespace only', () => {
    expect(buildCsvHeader('Status', '   ', 'Description')).toBe('Status')
  })

  it('appends a bracketed marker when a description is present', () => {
    expect(buildCsvHeader('Status', 'Workflow stage', 'Description')).toBe(
      'Status [Description: Workflow stage]',
    )
  })

  it('trims the description before embedding it', () => {
    expect(buildCsvHeader('Status', '  Workflow stage  ', 'Description')).toBe(
      'Status [Description: Workflow stage]',
    )
  })

  it('uses the supplied prefix label verbatim', () => {
    expect(buildCsvHeader('Status', 'Stage', 'Note')).toBe('Status [Note: Stage]')
  })

  it('tolerates an undefined description (defensive)', () => {
    expect(buildCsvHeader('Status', undefined as unknown as string, 'Description')).toBe('Status')
  })
})

// ── buildRelationKeyResolver ──────────────────────────────────────────────────
//
// The export has no component context, so the key lookups a keyed relation
// needs are built up front from the target databases already loaded for the
// title resolver. The mapping itself is pure and tested here; wiring it into
// displayValue is covered by the cellUtils suite.

import { buildRelationKeyResolver } from '@/composables/useExport'
import type { DatabaseEntry, PropertySchema } from '@/stores/database'

function schema(id: string, type = 'date'): PropertySchema {
  return {
    id,
    database_id: 'db-plotbeats',
    name: id,
    type,
    config: {},
    position: 0,
    group: 'Standard',
  }
}

function entry(
  id: string,
  values: Record<string, Record<string, unknown> | null>,
): DatabaseEntry {
  return { id, position: 0, content: null, icon: null, state: 'active', values }
}

describe('buildRelationKeyResolver', () => {
  it('resolves a key property by id', () => {
    const resolver = buildRelationKeyResolver([schema('schema-date')], [])
    expect(resolver.schemaFor('schema-date')?.id).toBe('schema-date')
    expect(resolver.schemaFor('schema-missing')).toBeNull()
  })

  it('resolves a key value by property and entry', () => {
    const resolver = buildRelationKeyResolver(
      [schema('schema-date')],
      [entry('beat-1', { 'schema-date': { start: '2136-08-14' } })],
    )
    expect(resolver.valueFor('schema-date', 'beat-1')).toEqual({ start: '2136-08-14' })
  })

  it('returns null for an entry that stores no value for the property', () => {
    const resolver = buildRelationKeyResolver(
      [schema('schema-date')],
      [entry('beat-1', {})],
    )
    expect(resolver.valueFor('schema-date', 'beat-1')).toBeNull()
  })

  it('returns null for an unknown entry', () => {
    const resolver = buildRelationKeyResolver([schema('schema-date')], [])
    expect(resolver.valueFor('schema-date', 'nobody')).toBeNull()
  })

  it('keys values by property and entry, so two properties never collide', () => {
    const resolver = buildRelationKeyResolver(
      [schema('schema-date'), schema('schema-rank', 'number')],
      [entry('beat-1', { 'schema-date': { start: '2136-08-14' }, 'schema-rank': { number: 3 } })],
    )
    expect(resolver.valueFor('schema-date', 'beat-1')).toEqual({ start: '2136-08-14' })
    expect(resolver.valueFor('schema-rank', 'beat-1')).toEqual({ number: 3 })
  })

  it('flattens several target databases into one lookup', () => {
    const foreign = { ...schema('schema-order', 'number'), database_id: 'db-other' }
    const resolver = buildRelationKeyResolver(
      [schema('schema-date'), foreign],
      [
        entry('beat-1', { 'schema-date': { start: '2136-08-14' } }),
        entry('other-1', { 'schema-order': { number: 7 } }),
      ],
    )
    expect(resolver.schemaFor('schema-order')?.database_id).toBe('db-other')
    expect(resolver.valueFor('schema-order', 'other-1')).toEqual({ number: 7 })
  })

  it('skips null values rather than caching them as objects', () => {
    const resolver = buildRelationKeyResolver(
      [schema('schema-date')],
      [entry('beat-1', { 'schema-date': null })],
    )
    expect(resolver.valueFor('schema-date', 'beat-1')).toBeNull()
  })

  it('tolerates an entry without a values map', () => {
    const bare = { id: 'beat-1', position: 0, content: null, icon: null, state: 'active' }
    const resolver = buildRelationKeyResolver(
      [schema('schema-date')],
      [bare as unknown as DatabaseEntry],
    )
    expect(resolver.valueFor('schema-date', 'beat-1')).toBeNull()
  })

  it('yields an inert resolver for empty input', () => {
    const resolver = buildRelationKeyResolver([], [])
    expect(resolver.schemaFor('schema-date')).toBeNull()
    expect(resolver.valueFor('schema-date', 'beat-1')).toBeNull()
  })
})

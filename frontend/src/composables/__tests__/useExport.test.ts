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

// ── buildEntryTitleLookup ─────────────────────────────────────────────────────
//
// The listing behind the export only contains active entries. Once every
// target database has been loaded in full, an id that is not among them
// belongs to a trashed or deleted entry and resolves to null, so the export
// omits the link. If a target database could not be loaded, a missing id is
// indistinguishable from an unloaded one and resolves to '' (raw id kept).

import { buildEntryTitleLookup } from '@/composables/useExport'

function titled(id: string, title: string | null): DatabaseEntry {
  return {
    id,
    position: 0,
    content: title === null ? null : { title },
    icon: null,
    state: 'active',
    values: {},
  }
}

describe('buildEntryTitleLookup', () => {
  it('resolves loaded entries to their titles', () => {
    const lookup = buildEntryTitleLookup([titled('e1', 'First')], 'Untitled', true)
    expect(lookup('e1')).toBe('First')
  })

  it('uses the untitled label for an empty or missing title', () => {
    const lookup = buildEntryTitleLookup(
      [titled('e1', '   '), titled('e2', null)],
      'Untitled',
      true,
    )
    expect(lookup('e1')).toBe('Untitled')
    expect(lookup('e2')).toBe('Untitled')
  })

  it('returns null for an id absent from complete listings (trashed entry)', () => {
    const lookup = buildEntryTitleLookup([titled('e1', 'First')], 'Untitled', true)
    expect(lookup('trashed')).toBeNull()
  })

  it('returns an empty string for an absent id when a listing failed to load', () => {
    const lookup = buildEntryTitleLookup([titled('e1', 'First')], 'Untitled', false)
    expect(lookup('unknown')).toBe('')
    expect(lookup('e1')).toBe('First')
  })
})

// ── View description in CSV / XLSX headers ────────────────────────────────────
//
// The view description rides on the header of the lead property, the name
// column, wherever the view has moved it; only while the name column is hidden
// does the first exported column take it. A view without a description exports
// exactly as before.

import {
  appendViewDescriptionMarker,
  buildExportHeaderRow,
  viewDescriptionColumnIndex,
} from '@/composables/useExport'
import { NAME_COL_KEY } from '@/stores/database'

describe('appendViewDescriptionMarker', () => {
  it('returns the header unchanged for an empty description', () => {
    expect(appendViewDescriptionMarker('Name', '', 'Global description')).toBe('Name')
    expect(appendViewDescriptionMarker('Name', '  \n ', 'Global description')).toBe('Name')
  })

  it('appends a bracketed marker', () => {
    expect(appendViewDescriptionMarker('Name', 'Open tasks', 'Global description')).toBe(
      'Name [Global description: Open tasks]',
    )
  })

  it('collapses line breaks into single spaces', () => {
    expect(
      appendViewDescriptionMarker('Name', 'First line\n\n  Second line', 'Global description'),
    ).toBe('Name [Global description: First line Second line]')
  })

  it('uses the supplied prefix verbatim', () => {
    expect(appendViewDescriptionMarker('Name', 'x', 'Note')).toBe('Name [Note: x]')
  })
})

describe('viewDescriptionColumnIndex', () => {
  it('finds the name column at the front', () => {
    expect(viewDescriptionColumnIndex([NAME_COL_KEY, 'schema-a'])).toBe(0)
  })

  it('finds the name column wherever the view has moved it', () => {
    expect(viewDescriptionColumnIndex(['schema-id', NAME_COL_KEY, 'schema-a'])).toBe(1)
    expect(viewDescriptionColumnIndex(['schema-id', 'schema-a', NAME_COL_KEY])).toBe(2)
  })

  it('falls back to the first column while the name column is hidden', () => {
    expect(viewDescriptionColumnIndex(['schema-id', 'schema-a'])).toBe(0)
  })

  it('returns -1 when no column is exported', () => {
    expect(viewDescriptionColumnIndex([])).toBe(-1)
  })
})

describe('buildExportHeaderRow', () => {
  const P = 'Description'
  const V = 'Global description'

  it('leaves headers untouched without any descriptions', () => {
    expect(buildExportHeaderRow(['Name', 'Status'], ['', ''], 0, '', P, V)).toEqual(['Name', 'Status'])
  })

  it('puts the view description on the lead column only', () => {
    expect(buildExportHeaderRow(['Name', 'Status'], ['', ''], 0, 'Open tasks', P, V)).toEqual([
      'Name [Global description: Open tasks]',
      'Status',
    ])
  })

  it('targets the name column when another column comes first', () => {
    // A view that places an ID column in front of the name column.
    expect(
      buildExportHeaderRow(['ID', 'Name', 'Status'], ['', '', ''], 1, 'Open tasks', P, V),
    ).toEqual(['ID', 'Name [Global description: Open tasks]', 'Status'])
  })

  it('keeps property descriptions on every column', () => {
    expect(
      buildExportHeaderRow(['Name', 'Status'], ['', 'Workflow stage'], 0, '', P, V),
    ).toEqual(['Name', 'Status [Description: Workflow stage]'])
  })

  it('places the view marker after the stand-in column\'s own description', () => {
    // With the name column hidden, the first exported column is a property
    // that may carry a description of its own.
    expect(
      buildExportHeaderRow(['Status'], ['Workflow stage'], 0, 'Open tasks', P, V),
    ).toEqual(['Status [Description: Workflow stage] [Global description: Open tasks]'])
  })

  it('adds no marker when no column is exported', () => {
    expect(buildExportHeaderRow([], [], -1, 'Open tasks', P, V)).toEqual([])
  })
})

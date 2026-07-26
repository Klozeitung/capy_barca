import { describe, it, expect } from 'vitest'
import { displayValue, formatPeriodKey, getTimelineDisplayMode } from '../cellUtils'
import type { DatabaseEntry, PropertySchema } from '@/stores/database'

// ── Fixtures ──────────────────────────────────────────────────────────────────

function relationSchema(config: Record<string, unknown> = {}): PropertySchema {
  return {
    id: 'schema-partner',
    database_id: 'db-1',
    name: 'Partner',
    type: 'relation',
    config: {
      hasTimeline: true,
      timelineDisplayMode: 'all',
      target_database_id: 'db-1',
      ...config,
    },
    position: 0,
    group: 'Standard',
  }
}

function entryWithPartner(value: Record<string, unknown> | null): DatabaseEntry {
  return {
    id: 'entry-1',
    position: 0,
    content: { title: 'Lete' },
    icon: null,
    state: 'active',
    values: { 'schema-partner': value },
  }
}

const TITLES: Record<string, string> = {
  'torik-id': 'Torik',
  'irena-id': 'Irena',
}
const resolveTitle = (id: string): string => TITLES[id] ?? ''

// ── Timeline relation export ("all" mode) ─────────────────────────────────────

describe('displayValue – timeline relation export ("all" mode)', () => {
  it('resolves relation chips to titles for each bounded slot', () => {
    const value = {
      relationPool: {
        'torik-id': ['2136-08-14T17:13:00→2137-05-04T00:00:00'],
        'irena-id': ['2143-01-29T00:00:00→2174-09-01T00:00:00'],
      },
      _timeline: {
        '2136-08-14T17:13:00→2137-05-04T00:00:00': { related_ids: ['torik-id'] },
        '2143-01-29T00:00:00→2174-09-01T00:00:00': { related_ids: ['irena-id'] },
      },
    }
    expect(
      displayValue(entryWithPartner(value), relationSchema(), undefined, resolveTitle),
    ).toBe('14.08.2136 17:13 → 04.05.2137: Torik · 29.01.2143 → 01.09.2174: Irena')
  })

  it('renders an always-valid ("") slot as names only, without a period', () => {
    const value = { _timeline: { '': { related_ids: ['torik-id'] } } }
    expect(
      displayValue(entryWithPartner(value), relationSchema(), undefined, resolveTitle),
    ).toBe('Torik')
  })

  it('comma-joins multiple linked entries within a single slot', () => {
    const value = { _timeline: { '': { related_ids: ['torik-id', 'irena-id'] } } }
    expect(
      displayValue(entryWithPartner(value), relationSchema(), undefined, resolveTitle),
    ).toBe('Torik, Irena')
  })

  it('falls back to the raw id when a title cannot be resolved', () => {
    const value = { _timeline: { '': { related_ids: ['unknown-id'] } } }
    expect(
      displayValue(entryWithPartner(value), relationSchema(), undefined, resolveTitle),
    ).toBe('unknown-id')
  })

  it('falls back to raw ids when no resolver is supplied', () => {
    const value = { _timeline: { '': { related_ids: ['torik-id'] } } }
    expect(displayValue(entryWithPartner(value), relationSchema())).toBe('torik-id')
  })

  it('emits the period only for a slot with no linked entries', () => {
    const value = {
      _timeline: {
        '2136-08-14T17:13:00→2137-05-04T00:00:00': { related_ids: ['torik-id'] },
        '2138-01-01T00:00:00→2139-01-01T00:00:00': { related_ids: [] },
      },
    }
    expect(
      displayValue(entryWithPartner(value), relationSchema(), undefined, resolveTitle),
    ).toBe('14.08.2136 17:13 → 04.05.2137: Torik · 01.01.2138 → 01.01.2139')
  })
})

// ── Regression guard: scalar "all" mode must stay intact ──────────────────────

describe('displayValue – timeline scalar export ("all" mode) is unaffected', () => {
  it('still renders select slots as "period: value"', () => {
    const schema: PropertySchema = {
      id: 'schema-status',
      database_id: 'db-1',
      name: 'Status',
      type: 'select',
      config: { hasTimeline: true, timelineDisplayMode: 'all', mode: 'single' },
      position: 0,
      group: 'Standard',
    }
    const entry: DatabaseEntry = {
      id: 'entry-2',
      position: 0,
      content: null,
      icon: null,
      state: 'active',
      values: {
        'schema-status': {
          _timeline: {
            '1996-06-27T00:00:00→2019-08-22T00:00:00': { option: 'Aktiv' },
            '2019-08-22T00:00:00→2020-01-30T00:00:00': { option: 'Nonexistent' },
          },
        },
      },
    }
    expect(displayValue(entry, schema)).toBe(
      '27.06.1996 → 22.08.2019: Aktiv · 22.08.2019 → 30.01.2020: Nonexistent',
    )
  })
})

// ── Relation nuance ───────────────────────────────────────────────────────────

import {
  getNuanceConfig,
  nuanceLabelFor,
  formatNuancedRelation,
  type NuanceConfig,
} from '../cellUtils'

function nuanceConfig(over: Partial<NuanceConfig> = {}): NuanceConfig {
  return { enabled: true, options: [], affix1: '', affix2: '', orientation: 'prepended', ...over }
}

describe('getNuanceConfig', () => {
  it('returns null when nuance is absent or disabled', () => {
    expect(getNuanceConfig(relationSchema())).toBeNull()
    expect(getNuanceConfig(relationSchema({ nuance: { enabled: false } }))).toBeNull()
  })

  it('reads affixes and orientation, defaulting orientation to prepended', () => {
    const cfg = getNuanceConfig(relationSchema({
      nuance: { enabled: true, affix1: 'as', affix2: 'of', orientation: 'appended' },
    }))
    expect(cfg).toMatchObject({ affix1: 'as', affix2: 'of', orientation: 'appended' })

    const cfg2 = getNuanceConfig(relationSchema({ nuance: { enabled: true } }))
    expect(cfg2?.orientation).toBe('prepended')
  })
})

describe('formatNuancedRelation', () => {
  const cfg = nuanceConfig

  it('returns the bare title when there is no label or no config', () => {
    expect(formatNuancedRelation('Torik', '', cfg())).toBe('Torik')
    expect(formatNuancedRelation('Torik', 'lead', null)).toBe('Torik')
  })

  it('prepends the nuance group before the title (prepended)', () => {
    expect(formatNuancedRelation('Großmeister', 'erfolgreich', cfg())).toBe('erfolgreich Großmeister')
    expect(
      formatNuancedRelation('Großmeister', 'erfolgreich', cfg({ affix1: 'als', affix2: 'gewählt' })),
    ).toBe('als erfolgreich gewählt Großmeister')
  })

  it('appends the nuance group after the title (appended)', () => {
    expect(
      formatNuancedRelation('Großmeister', 'erfolgreich', cfg({ orientation: 'appended', affix1: 'als', affix2: 'bestätigt' })),
    ).toBe('Großmeister als erfolgreich bestätigt')
  })
})

describe('nuanceLabelFor', () => {
  it('reads the label for a related id, empty when missing', () => {
    const slot = { related_ids: ['a'], nuances: { a: 'lead' } }
    expect(nuanceLabelFor(slot, 'a')).toBe('lead')
    expect(nuanceLabelFor(slot, 'b')).toBe('')
    expect(nuanceLabelFor(null, 'a')).toBe('')
  })
})

describe('displayValue – nuanced timeline relation', () => {
  it('renders the nuance group in an always-valid slot ("all" mode)', () => {
    const schema = relationSchema({ nuance: nuanceConfig() })
    const value = { _timeline: { '': { related_ids: ['torik-id'], nuances: { 'torik-id': 'erfolgreich' } } } }
    expect(displayValue(entryWithPartner(value), schema, undefined, resolveTitle)).toBe('erfolgreich Torik')
  })

  it('renders nuance per related entry within a bounded slot', () => {
    const schema = relationSchema({ nuance: nuanceConfig({ orientation: 'appended', affix2: 'gewählt' }) })
    const value = {
      _timeline: {
        '2136-08-14T17:13:00→2137-05-04T00:00:00': {
          related_ids: ['torik-id', 'irena-id'],
          nuances: { 'torik-id': 'erfolgreich' },
        },
      },
    }
    expect(displayValue(entryWithPartner(value), schema, undefined, resolveTitle)).toBe(
      '14.08.2136 17:13 → 04.05.2137: Torik erfolgreich gewählt, Irena',
    )
  })
})

describe('displayValue – nuanced flat relation ("last" mode)', () => {
  it('renders the nuance group for a non-timeline relation', () => {
    const schema: PropertySchema = {
      id: 'schema-partner',
      database_id: 'db-1',
      name: 'Partner',
      type: 'relation',
      config: { target_database_id: 'db-1', nuance: nuanceConfig() },
      position: 0,
      group: 'Standard',
    }
    const value = { related_ids: ['torik-id'], nuances: { 'torik-id': 'erfolgreich' } }
    expect(displayValue(entryWithPartner(value), schema, undefined, resolveTitle)).toBe('erfolgreich Torik')
  })
})

// ── formatPeriodKey ───────────────────────────────────────────────────────────
//
// Timeline period boundaries must honour the user's date-format preference
// instead of the previous hard-coded ISO ``slice(0, 10)``. The format is read
// from the auth store; an explicit token can be passed for deterministic tests.
// Outside an active Pinia instance the store read is guarded and degrades to
// the DD.MM.YYYY fallback.

describe('formatPeriodKey', () => {
  it('renders the always-valid key as the infinity sentinel', () => {
    expect(formatPeriodKey('')).toBe('∞')
    expect(formatPeriodKey('', 'YYYY-MM-DD')).toBe('∞')
  })

  it('formats both boundaries with an explicit user format', () => {
    expect(
      formatPeriodKey('2136-08-14T00:00:00→2137-05-04T00:00:00', 'DD.MM.YYYY'),
    ).toBe('14.08.2136 → 04.05.2137')
    expect(
      formatPeriodKey('2136-08-14T00:00:00→2137-05-04T00:00:00', 'YYYY-MM-DD'),
    ).toBe('2136-08-14 → 2137-05-04')
    expect(
      formatPeriodKey('2136-08-14T00:00:00→2137-05-04T00:00:00', 'MM.DD.YYYY'),
    ).toBe('08.14.2136 → 05.04.2137')
  })

  it('shows the time component only when it is not midnight', () => {
    expect(
      formatPeriodKey('2136-08-14T17:13:00→2137-05-04T00:00:00', 'DD.MM.YYYY'),
    ).toBe('14.08.2136 17:13 → 04.05.2137')
  })

  it('renders an open-ended (since) range', () => {
    expect(formatPeriodKey('2136-08-14T00:00:00→', 'DD.MM.YYYY')).toBe('14.08.2136 →')
    expect(formatPeriodKey('2136-08-14T17:13:00→', 'DD.MM.YYYY')).toBe('14.08.2136 17:13 →')
  })

  it('renders an until range', () => {
    expect(formatPeriodKey('→2137-05-04T00:00:00', 'DD.MM.YYYY')).toBe('→ 04.05.2137')
    expect(formatPeriodKey('→2137-05-04T09:30:00', 'YYYY-MM-DD')).toBe('→ 2137-05-04 09:30')
  })

  it('falls back to DD.MM.YYYY when no user format is available', () => {
    expect(
      formatPeriodKey('2136-08-14T00:00:00→2137-05-04T00:00:00'),
    ).toBe('14.08.2136 → 04.05.2137')
  })
})

// ── getTimelineDisplayMode ─────────────────────────────────────────────────────
//
// Timelined properties default to "all" (every slot shown as a period → value
// row). An explicit config.timelineDisplayMode always wins. The "last"/non-set
// distinction matters because the cells gate their slot-list rendering on the
// resolved mode being exactly 'all'.

describe('getTimelineDisplayMode', () => {
  function schemaWith(config: Record<string, unknown>): PropertySchema {
    return {
      id: 'schema-x',
      database_id: 'db-1',
      name: 'X',
      type: 'text',
      config,
      position: 0,
      group: 'Standard',
    }
  }

  it('defaults to "all" when no display mode is set', () => {
    expect(getTimelineDisplayMode(schemaWith({ hasTimeline: true }))).toBe('all')
    expect(getTimelineDisplayMode(schemaWith({}))).toBe('all')
  })

  it('respects an explicit mode', () => {
    expect(getTimelineDisplayMode(schemaWith({ timelineDisplayMode: 'last' }))).toBe('last')
    expect(getTimelineDisplayMode(schemaWith({ timelineDisplayMode: 'all' }))).toBe('all')
    expect(getTimelineDisplayMode(schemaWith({ timelineDisplayMode: 'now' }))).toBe('now')
  })
})

// ── Formula date-range result ─────────────────────────────────────────────────
//
// A formula that yields a date property directly (prop('date')) produces a
// {start, end} object, not an ISO string. It must render like a date cell
// ("start → end") instead of leaking raw JSON. The type guard also has to
// reject relation descriptors, which are objects too but carry no start.

import {
  isDateRangeResult,
  formatDateRangeResult,
  formatFormulaExport,
} from '../cellUtils'

describe('isDateRangeResult', () => {
  it('accepts a {start, end} object with an ISO start', () => {
    expect(isDateRangeResult({ start: '2000-01-01T00:01', end: '2000-01-02T00:01' })).toBe(true)
    expect(isDateRangeResult({ start: '2000-01-01', end: null })).toBe(true)
  })

  it('rejects non-date-range shapes', () => {
    expect(isDateRangeResult(null)).toBe(false)
    expect(isDateRangeResult('2000-01-01')).toBe(false)
    expect(isDateRangeResult(['2000-01-01'])).toBe(false)
    expect(isDateRangeResult({ id: 'u1', title: 'X', database_id: 'db' })).toBe(false)
    expect(isDateRangeResult({ start: 'not-a-date' })).toBe(false)
  })
})

describe('formatDateRangeResult', () => {
  it('renders a single boundary when there is no distinct end', () => {
    expect(formatDateRangeResult({ start: '2000-01-01T00:01', end: null }, 'DD.MM.YYYY')).toBe('01.01.2000 00:01')
    expect(formatDateRangeResult({ start: '2000-01-01T00:01', end: '2000-01-01T00:01' }, 'DD.MM.YYYY')).toBe('01.01.2000 00:01')
  })

  it('renders "start → end" for a distinct end', () => {
    expect(
      formatDateRangeResult({ start: '2000-01-01T00:01', end: '2000-01-02T00:01' }, 'DD.MM.YYYY'),
    ).toBe('01.01.2000 00:01 → 02.01.2000 00:01')
  })

  it('drops midnight time components', () => {
    expect(
      formatDateRangeResult({ start: '2000-01-01T00:00:00', end: '2000-01-02T00:00:00' }, 'DD.MM.YYYY'),
    ).toBe('01.01.2000 → 02.01.2000')
  })

  it('returns empty when there is no start', () => {
    expect(formatDateRangeResult({ start: '', end: null }, 'DD.MM.YYYY')).toBe('')
  })
})

describe('formatFormulaExport – date-range object result', () => {
  function formulaSchema(): PropertySchema {
    return {
      id: 'schema-f',
      database_id: 'db-1',
      name: 'F',
      type: 'formula',
      config: { expression: "prop('date')", dateFormat: 'DD.MM.YYYY' },
      position: 0,
      group: 'Standard',
    }
  }

  it('formats a {start, end} result instead of leaking JSON', () => {
    const value = {
      result: { start: '2000-01-01T00:01', end: '2000-01-02T00:01' },
      result_type: 'date',
    }
    expect(formatFormulaExport(value, formulaSchema())).toBe('01.01.2000 00:01 → 02.01.2000 00:01')
  })

  it('formats a start-only result as a single date', () => {
    const value = { result: { start: '2000-01-01T00:00:00', end: null }, result_type: 'date' }
    expect(formatFormulaExport(value, formulaSchema())).toBe('01.01.2000')
  })
})

// ── Relation keying ───────────────────────────────────────────────────────────
//
// Keying orders a relation's linked entries by a property of the target
// database and renders that value beside each chip. Everything below is a pure
// view transform: the caller supplies the key values, and the id array handed in
// is never mutated.

import {
  getKeyingConfig,
  isKeyableProperty,
  keyComparable,
  sortIdsByKey,
  formatKeyValue,
  KEYABLE_PROPERTY_TYPES,
  type KeyingConfig,
} from '../cellUtils'

function targetSchema(type: string, config: Record<string, unknown> = {}): PropertySchema {
  return {
    id: `schema-${type}`,
    database_id: 'db-target',
    name: type,
    type,
    config,
    position: 0,
    group: 'Standard',
  }
}

function keyingSchema(over: Record<string, unknown> = {}): PropertySchema {
  return {
    id: 'schema-plot',
    database_id: 'db-1',
    name: 'Plot',
    type: 'relation',
    config: {
      target_database_id: 'db-target',
      keying: {
        enabled: true,
        key_property_id: 'schema-date',
        key_order: 'asc',
        key_empty_first: false,
        ...over,
      },
    },
    position: 0,
    group: 'Standard',
  }
}

const ASC: KeyingConfig = { keyPropertyId: 'schema-date', order: 'asc', emptyFirst: false }

describe('isKeyableProperty', () => {
  it('accepts exactly the four supported types', () => {
    for (const type of KEYABLE_PROPERTY_TYPES) {
      expect(isKeyableProperty(targetSchema(type))).toBe(true)
    }
  })

  it('rejects everything else', () => {
    for (const type of ['checkbox', 'relation', 'rollup', 'formula', 'file', 'id']) {
      expect(isKeyableProperty(targetSchema(type))).toBe(false)
    }
  })
})

describe('getKeyingConfig', () => {
  it('returns null when keying is absent or disabled', () => {
    const bare: PropertySchema = { ...keyingSchema(), config: { target_database_id: 'db-target' } }
    expect(getKeyingConfig(bare)).toBeNull()
    expect(getKeyingConfig(keyingSchema({ enabled: false }))).toBeNull()
  })

  it('treats an enabled block without a property id as absent', () => {
    expect(getKeyingConfig(keyingSchema({ key_property_id: '' }))).toBeNull()
  })

  it('reads the pointer, order and empty placement', () => {
    expect(getKeyingConfig(keyingSchema())).toEqual({
      keyPropertyId: 'schema-date',
      order: 'asc',
      emptyFirst: false,
    })
    expect(
      getKeyingConfig(keyingSchema({ key_order: 'desc', key_empty_first: true })),
    ).toMatchObject({ order: 'desc', emptyFirst: true })
  })

  it('defaults an unknown order to ascending', () => {
    expect(getKeyingConfig(keyingSchema({ key_order: 'sideways' }))?.order).toBe('asc')
  })
})

describe('keyComparable', () => {
  it('reads a date property\'s start boundary', () => {
    const schema = targetSchema('date')
    expect(keyComparable({ start: '2136-08-14' }, schema)).toBe('2136-08-14')
    expect(keyComparable({ start: '' }, schema)).toBeNull()
    expect(keyComparable({ end: '2136-08-14' }, schema)).toBeNull()
  })

  it('reads a number property numerically, including zero', () => {
    const schema = targetSchema('number')
    expect(keyComparable({ number: 12 }, schema)).toBe(12)
    expect(keyComparable({ number: 0 }, schema)).toBe(0)
    expect(keyComparable({ number: '7' }, schema)).toBe(7)
    expect(keyComparable({ number: 'x' }, schema)).toBeNull()
    expect(keyComparable({}, schema)).toBeNull()
  })

  it('reads a select property as its option index, not its label', () => {
    const schema = targetSchema('select', { options: ['Act III', 'Act I', 'Act II'] })
    expect(keyComparable({ option: 'Act III' }, schema)).toBe(0)
    expect(keyComparable({ option: 'Act I' }, schema)).toBe(1)
    expect(keyComparable({ option: 'Retired' }, schema)).toBeNull()
  })

  it('reads a multi-select by its earliest option', () => {
    const schema = targetSchema('select', {
      mode: 'multiple',
      options: [{ label: 'A' }, { label: 'B' }, { label: 'C' }],
    })
    expect(keyComparable({ options: ['C', 'B'] }, schema)).toBe(1)
    expect(keyComparable({ options: [] }, schema)).toBeNull()
  })

  it('reads a text property, treating whitespace as empty', () => {
    const schema = targetSchema('text')
    expect(keyComparable({ text: 'Aleph' }, schema)).toBe('Aleph')
    expect(keyComparable({ text: '   ' }, schema)).toBeNull()
  })

  it('returns null without a value or a schema', () => {
    expect(keyComparable(null, targetSchema('date'))).toBeNull()
    expect(keyComparable({ start: '2136-08-14' }, null)).toBeNull()
  })
})

describe('sortIdsByKey', () => {
  const dateSchema = targetSchema('date')
  const dates: Record<string, Record<string, unknown> | null> = {
    late: { start: '2174-09-01' },
    early: { start: '2136-08-14' },
    middle: { start: '2143-01-29' },
    undated: null,
  }
  const lookup = (id: string) => dates[id] ?? null

  it('orders ascending by the key value', () => {
    expect(sortIdsByKey(['late', 'early', 'middle'], lookup, dateSchema, ASC)).toEqual([
      'early', 'middle', 'late',
    ])
  })

  it('orders descending', () => {
    expect(
      sortIdsByKey(['early', 'late', 'middle'], lookup, dateSchema, { ...ASC, order: 'desc' }),
    ).toEqual(['late', 'middle', 'early'])
  })

  it('places entries without a key value last by default', () => {
    expect(sortIdsByKey(['undated', 'late', 'early'], lookup, dateSchema, ASC)).toEqual([
      'early', 'late', 'undated',
    ])
  })

  it('places them first when emptyFirst is set', () => {
    expect(
      sortIdsByKey(['late', 'undated', 'early'], lookup, dateSchema, { ...ASC, emptyFirst: true }),
    ).toEqual(['undated', 'early', 'late'])
  })

  it('keeps the empty placement independent of the order direction', () => {
    const desc = { ...ASC, order: 'desc' as const }
    expect(sortIdsByKey(['undated', 'early', 'late'], lookup, dateSchema, desc)).toEqual([
      'late', 'early', 'undated',
    ])
  })

  it('breaks ties by the incoming (seniority) order', () => {
    const tied: Record<string, Record<string, unknown>> = {
      b: { start: '2140-01-01' },
      a: { start: '2140-01-01' },
      c: { start: '2130-01-01' },
    }
    expect(
      sortIdsByKey(['b', 'a', 'c'], (id: string) => tied[id], dateSchema, ASC),
    ).toEqual(['c', 'b', 'a'])
  })

  it('keeps the incoming order among entries without a key value', () => {
    expect(
      sortIdsByKey(['z', 'y', 'early'], lookup, dateSchema, { ...ASC, emptyFirst: true }),
    ).toEqual(['z', 'y', 'early'])
  })

  it('returns the input unchanged when the key property cannot be resolved', () => {
    const input = ['late', 'early']
    expect(sortIdsByKey(input, lookup, null, ASC)).toEqual(input)
  })

  it('does not mutate the array it was given', () => {
    const input = ['late', 'early', 'middle']
    sortIdsByKey(input, lookup, dateSchema, ASC)
    expect(input).toEqual(['late', 'early', 'middle'])
  })

  it('sorts numbers numerically rather than lexicographically', () => {
    const numberSchema = targetSchema('number')
    const values: Record<string, Record<string, unknown>> = {
      ten: { number: 10 },
      two: { number: 2 },
      hundred: { number: 100 },
    }
    expect(
      sortIdsByKey(['ten', 'hundred', 'two'], (id: string) => values[id], numberSchema, ASC),
    ).toEqual(['two', 'ten', 'hundred'])
  })

  it('sorts selects by option order rather than alphabetically', () => {
    const selectSchema = targetSchema('select', { options: ['Zeta', 'Alpha'] })
    const values: Record<string, Record<string, unknown>> = {
      alpha: { option: 'Alpha' },
      zeta: { option: 'Zeta' },
    }
    expect(
      sortIdsByKey(['alpha', 'zeta'], (id: string) => values[id], selectSchema, ASC),
    ).toEqual(['zeta', 'alpha'])
  })
})

describe('formatKeyValue', () => {
  it('renders a date with the key property\'s own format', () => {
    const schema = targetSchema('date', { dateFormat: 'YYYY-MM-DD' })
    expect(formatKeyValue({ start: '2136-08-14T00:00:00' }, schema)).toBe('2136-08-14')
  })

  it('renders a select label and a plain number', () => {
    expect(formatKeyValue({ option: 'Act I' }, targetSchema('select'))).toBe('Act I')
    expect(formatKeyValue({ number: 12 }, targetSchema('number'))).toBe('12')
  })

  it('returns empty without a value or a schema', () => {
    expect(formatKeyValue(null, targetSchema('date'))).toBe('')
    expect(formatKeyValue({ start: '2136-08-14' }, null)).toBe('')
  })
})

// ── Keyed relation export ─────────────────────────────────────────────────────
//
// displayValue is the plain-text path behind CSV, XLSX and PDF. A keyed
// relation must export in key order with each entry prefixed by its key value,
// mirroring the two-zone cell — and must fall back to the vanilla rendering for
// every caller that supplies no resolver.

import type { RelationKeyResolver } from '../cellUtils'

function keyedRelationSchema(over: Record<string, unknown> = {}): PropertySchema {
  return {
    id: 'schema-plot',
    database_id: 'db-characters',
    name: 'Plot',
    type: 'relation',
    config: {
      target_database_id: 'db-plotbeats',
      keying: {
        enabled: true,
        key_property_id: 'schema-beatdate',
        key_order: 'asc',
        key_empty_first: false,
        ...over,
      },
    },
    position: 0,
    group: 'Standard',
  }
}

function entryWithPlot(relatedIds: string[]): DatabaseEntry {
  return {
    id: 'character-1',
    position: 0,
    content: { title: 'Lete' },
    icon: null,
    state: 'active',
    values: { 'schema-plot': { related_ids: relatedIds } },
  }
}

const BEAT_TITLES: Record<string, string> = {
  'beat-late': 'Die Konfrontation',
  'beat-early': 'Der Aufbruch',
  'beat-undated': 'Irgendwann in Akt 3',
}
const resolveBeatTitle = (id: string): string => BEAT_TITLES[id] ?? ''

const BEAT_DATES: Record<string, Record<string, unknown> | null> = {
  'beat-late': { start: '2143-01-29' },
  'beat-early': { start: '2136-08-14' },
  'beat-undated': null,
}

const beatDateSchema: PropertySchema = {
  id: 'schema-beatdate',
  database_id: 'db-plotbeats',
  name: 'Date',
  type: 'date',
  config: {},
  position: 0,
  group: 'Standard',
}

const keyResolver: RelationKeyResolver = {
  schemaFor: (id: string) => (id === 'schema-beatdate' ? beatDateSchema : null),
  valueFor: (keyPropertyId: string, entryId: string) =>
    keyPropertyId === 'schema-beatdate' ? (BEAT_DATES[entryId] ?? null) : null,
}

describe('displayValue – keyed relation export', () => {
  it('orders the linked entries by their key value and prefixes each', () => {
    expect(
      displayValue(
        entryWithPlot(['beat-late', 'beat-early']),
        keyedRelationSchema(),
        undefined,
        resolveBeatTitle,
        keyResolver,
      ),
    ).toBe('14.08.2136: Der Aufbruch · 29.01.2143: Die Konfrontation')
  })

  it('honours a descending order', () => {
    expect(
      displayValue(
        entryWithPlot(['beat-early', 'beat-late']),
        keyedRelationSchema({ key_order: 'desc' }),
        undefined,
        resolveBeatTitle,
        keyResolver,
      ),
    ).toBe('29.01.2143: Die Konfrontation · 14.08.2136: Der Aufbruch')
  })

  it('renders an entry without a key value as the bare title, placed last', () => {
    expect(
      displayValue(
        entryWithPlot(['beat-undated', 'beat-early']),
        keyedRelationSchema(),
        undefined,
        resolveBeatTitle,
        keyResolver,
      ),
    ).toBe('14.08.2136: Der Aufbruch · Irgendwann in Akt 3')
  })

  it('honours the empty-first placement', () => {
    expect(
      displayValue(
        entryWithPlot(['beat-early', 'beat-undated']),
        keyedRelationSchema({ key_empty_first: true }),
        undefined,
        resolveBeatTitle,
        keyResolver,
      ),
    ).toBe('Irgendwann in Akt 3 · 14.08.2136: Der Aufbruch')
  })

  it('falls back to the vanilla export when no resolver is supplied', () => {
    // Every existing caller passes four arguments and must be unaffected.
    expect(
      displayValue(
        entryWithPlot(['beat-late', 'beat-early']),
        keyedRelationSchema(),
        undefined,
        resolveBeatTitle,
      ),
    ).toBe('Die Konfrontation, Der Aufbruch')
  })

  it('falls back to the vanilla export when the key property no longer resolves', () => {
    const dangling: RelationKeyResolver = { schemaFor: () => null, valueFor: () => null }
    expect(
      displayValue(
        entryWithPlot(['beat-late', 'beat-early']),
        keyedRelationSchema(),
        undefined,
        resolveBeatTitle,
        dangling,
      ),
    ).toBe('Die Konfrontation, Der Aufbruch')
  })

  it('leaves a vanilla relation untouched even when a resolver is present', () => {
    const vanilla: PropertySchema = {
      ...keyedRelationSchema(),
      config: { target_database_id: 'db-plotbeats' },
    }
    expect(
      displayValue(
        entryWithPlot(['beat-late', 'beat-early']),
        vanilla,
        undefined,
        resolveBeatTitle,
        keyResolver,
      ),
    ).toBe('Die Konfrontation, Der Aufbruch')
  })

  it('prefers the established rendering when legacy data is keyed and nuanced', () => {
    const both = keyedRelationSchema()
    both.config = { ...both.config, nuance: nuanceConfig() }
    const entry = entryWithPlot(['beat-late'])
    entry.values['schema-plot'] = {
      related_ids: ['beat-late'],
      nuances: { 'beat-late': 'erfolgreich' },
    }
    expect(
      displayValue(entry, both, undefined, resolveBeatTitle, keyResolver),
    ).toBe('erfolgreich Die Konfrontation')
  })

  it('prefers the timeline rendering when legacy data is keyed and timelined', () => {
    const both = keyedRelationSchema()
    both.config = { ...both.config, hasTimeline: true, timelineDisplayMode: 'all' }
    const entry = entryWithPlot([])
    entry.values['schema-plot'] = { _timeline: { '': { related_ids: ['beat-early'] } } }
    expect(
      displayValue(entry, both, undefined, resolveBeatTitle, keyResolver),
    ).toBe('Der Aufbruch')
  })

  it('falls back to the raw id when a title cannot be resolved', () => {
    expect(
      displayValue(
        entryWithPlot(['beat-early']),
        keyedRelationSchema(),
        undefined,
        () => '',
        keyResolver,
      ),
    ).toBe('14.08.2136: beat-early')
  })

  it('returns empty for a relation with no links', () => {
    expect(
      displayValue(entryWithPlot([]), keyedRelationSchema(), undefined, resolveBeatTitle, keyResolver),
    ).toBe('')
  })

  it('formats the key with the key property\'s own configuration', () => {
    const isoSchema: PropertySchema = { ...beatDateSchema, config: { dateFormat: 'YYYY-MM-DD' } }
    const isoResolver: RelationKeyResolver = { ...keyResolver, schemaFor: () => isoSchema }
    expect(
      displayValue(
        entryWithPlot(['beat-early']),
        keyedRelationSchema(),
        undefined,
        resolveBeatTitle,
        isoResolver,
      ),
    ).toBe('2136-08-14: Der Aufbruch')
  })
})
